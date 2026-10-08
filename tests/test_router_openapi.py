"""The OpenAPI a router emits describes what the routes actually do.

GET on a collection and DELETE carried no response schema, and DELETE was
documented as 200 while it answers 204. No viewset route declared the bearer
security scheme or its 401/403/404/429 answers, so generated clients neither
sent tokens nor typed the errors. ``POST /query`` hard-coded ``limit <= 100``,
so raising ``MAX_LIMIT`` had no effect.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from zeeb_api.conf import settings
from zeeb_api.exception_handlers import install_error_response_schema, install_exception_handlers
from zeeb_api.pagination import LimitOffsetPagination
from zeeb_api.permissions import AllowAny, IsAuthenticated
from zeeb_api.routers.default import SimpleRouter
from zeeb_api.serializers import ModelSerializer
from zeeb_api.viewsets import ModelViewSet, ViewSet, action, extend_schema
from zeeb_orm import Model, fields


class DocPost(Model):
    title = fields.CharField(max_length=100)

    class Meta:
        table_name = "doc_posts"


class DocPostSerializer(ModelSerializer):
    class Meta:
        model = DocPost
        fields = ["id", "title"]


class Secured(ModelViewSet):
    queryset = DocPost.objects
    serializer_class = DocPostSerializer
    permission_classes = [IsAuthenticated]
    throttle_classes = []

    @action(detail=False, methods=["get"], permission_classes=[AllowAny])
    async def public(self, request):
        return {"ok": True}


class Paginated(Secured):
    pagination_class = LimitOffsetPagination


class Open(ModelViewSet):
    queryset = DocPost.objects
    serializer_class = DocPostSerializer
    throttle_classes = []


def _openapi(viewset) -> dict:
    router = SimpleRouter()
    router.register("posts", viewset)
    app = FastAPI()
    install_exception_handlers(app)
    install_error_response_schema(app)
    for api_router in router.get_urls():
        app.include_router(api_router)
    return app.openapi()


def _schema_ref(response: dict) -> dict:
    return response["content"]["application/json"]["schema"]


def test_list_documents_its_items():
    paths = _openapi(Secured)["paths"]
    schema = _schema_ref(paths["/posts"]["get"]["responses"]["200"])
    assert schema["type"] == "array"
    assert schema["items"]["$ref"].endswith("DocPostSerializerResponse")


def test_paginated_list_documents_the_envelope():
    spec = _openapi(Paginated)
    ref = _schema_ref(spec["paths"]["/posts"]["get"]["responses"]["200"])["$ref"]
    envelope = spec["components"]["schemas"][ref.rsplit("/", 1)[1]]
    assert set(envelope["properties"]) == {"count", "next", "previous", "results"}


def test_delete_is_documented_as_204():
    responses = _openapi(Secured)["paths"]["/posts/{id}"]["delete"]["responses"]
    assert "204" in responses
    assert "200" not in responses


def test_secured_routes_declare_bearer_and_error_answers():
    spec = _openapi(Secured)
    assert spec["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"
    detail = spec["paths"]["/posts/{id}"]["get"]
    assert detail["security"] == [{"HTTPBearer": []}]
    assert {"401", "403", "404"} <= set(detail["responses"])
    error_ref = _schema_ref(detail["responses"]["401"])["$ref"]
    assert error_ref.endswith("ErrorResponse")
    create = spec["paths"]["/posts"]["post"]["responses"]
    assert "400" in create and "404" not in create


def test_open_routes_declare_no_security():
    spec = _openapi(Secured)
    public = spec["paths"]["/posts/public"]["get"]
    assert "security" not in public
    assert "401" not in public["responses"]
    open_spec = _openapi(Open)
    assert "security" not in open_spec["paths"]["/posts"]["get"]


def test_throttled_routes_declare_429():
    class Throttled(Secured):
        throttle_classes = None  # DEFAULT_THROTTLE_CLASSES

    saved = settings.DEFAULT_THROTTLE_CLASSES
    settings.DEFAULT_THROTTLE_CLASSES = ["zeeb_api.throttling.AnonRateThrottle"]
    try:
        spec = _openapi(Throttled)
    finally:
        settings.DEFAULT_THROTTLE_CLASSES = saved
    assert "429" in spec["paths"]["/posts"]["get"]["responses"]
    assert "429" not in _openapi(Secured)["paths"]["/posts"]["get"]["responses"]


@pytest.fixture
def max_limit():
    saved = (settings.DEFAULT_LIMIT, settings.MAX_LIMIT)
    yield
    settings.DEFAULT_LIMIT, settings.MAX_LIMIT = saved


def test_query_limit_follows_max_limit(max_limit):
    settings.MAX_LIMIT = 500
    spec = _openapi(Open)
    ref = spec["paths"]["/posts/query"]["post"]["requestBody"]["content"]["application/json"][
        "schema"
    ]["$ref"]
    limit = spec["components"]["schemas"][ref.rsplit("/", 1)[1]]["properties"]["limit"]
    assert limit["maximum"] == 500


def test_query_accepts_a_limit_above_100_when_allowed(max_limit):
    settings.MAX_LIMIT = 500
    router = SimpleRouter()
    router.register("posts", Open)
    app = FastAPI()
    install_exception_handlers(app)
    for api_router in router.get_urls():
        app.include_router(api_router)

    captured = {}

    async def fake_query(self, request, **kwargs):
        captured.update(self._request_body)
        return {"count": 0, "limit": self._request_body["limit"], "offset": 0, "results": []}

    Open.query = fake_query
    try:
        client = TestClient(app)
        ok = client.post("/posts/query", json={"limit": 300})
        too_many = client.post("/posts/query", json={"limit": 501})
    finally:
        del Open.query
    assert ok.status_code == 200, ok.text
    assert captured["limit"] == 300
    assert too_many.status_code == 422


def test_destroy_override_with_a_body_still_sends_it():
    class Chatty(Open):
        async def destroy(self, request, **kwargs):
            return {"deleted": True}

    router = SimpleRouter()
    router.register("posts", Chatty)
    app = FastAPI()
    for api_router in router.get_urls():
        app.include_router(api_router)
    response = TestClient(app).delete("/posts/3fa85f64-5717-4562-b3fc-2c963f66afa6")
    assert response.status_code == 200
    assert response.json() == {"deleted": True}


def test_viewset_with_a_required_init_argument_still_routes():
    """Route building reads configuration off a request-less instance; a
    viewset whose __init__ needs extra arguments must not break it."""

    class NeedsArg(Open):
        def __init__(self, service, request=None, **kwargs):
            super().__init__(request=request, **kwargs)
            self.service = service

    spec = _openapi(NeedsArg)
    assert "/posts/{id}" in spec["paths"]


# --- error responses document their own status ------------------------------
#
# Every error response referenced one ErrorResponse schema whose only example
# was a VALIDATION_ERROR, so Swagger UI showed a validation error under 401,
# 404 and 429 alike; FastAPI's own 422 still documented HTTPValidationError.


def _error_examples(spec: dict) -> list[tuple[str, str, str, dict]]:
    found = []
    for path, item in spec["paths"].items():
        for method, operation in item.items():
            for status, response in operation["responses"].items():
                if status.isdigit() and int(status) >= 400:
                    media = response["content"]["application/json"]
                    found.append((path, method, status, media))
    return found


def test_each_error_response_shows_its_own_code():
    from zeeb_api.exceptions import STATUS_CODE_TO_ERROR_CODE

    spec = _openapi(Secured)
    examples = _error_examples(spec)
    assert examples
    for path, method, status, media in examples:
        assert media["schema"]["$ref"].endswith("ErrorResponse"), (path, method, status)
        example = media["example"]
        assert example["success"] is False
        assert example["error"]["code"] == STATUS_CODE_TO_ERROR_CODE[int(status)], (path, status)
    codes = {status: media["example"]["error"]["code"] for _, _, status, media in examples}
    assert codes["401"] == "AUTH_TOKEN_MISSING"
    assert codes["404"] == "RESOURCE_NOT_FOUND"


def test_error_response_schema_carries_no_validation_example():
    spec = _openapi(Secured)
    error_schema = spec["components"]["schemas"]["ErrorResponse"]
    assert "examples" not in error_schema and "example" not in error_schema


def test_validated_routes_document_the_envelope_422():
    spec = _openapi(Secured)
    assert "HTTPValidationError" not in spec["components"]["schemas"]
    create = spec["paths"]["/posts"]["post"]["responses"]
    detail = spec["paths"]["/posts/{id}"]["get"]["responses"]
    for responses in (create, detail):
        assert responses["422"]["description"] == "Request validation failed"
        example = responses["422"]["content"]["application/json"]["example"]
        assert example["error"]["details"][0]["code"] == "FIELD_REQUIRED"
    # A write may break a uniqueness constraint; a read cannot.
    assert "409" in create and "409" not in detail
    # The collection GET validates nothing.
    assert "422" not in spec["paths"]["/posts"]["get"]["responses"]


# --- built-in routes of a plain ViewSet, and per-action status -----------------


class _CreatePost(BaseModel):
    title: str


class _PostOut(BaseModel):
    id: str
    title: str


def _plain_app(viewset) -> FastAPI:
    router = SimpleRouter()
    router.register("posts", viewset)
    app = FastAPI()
    install_exception_handlers(app)
    install_error_response_schema(app)
    for api_router in router.get_urls():
        app.include_router(api_router)
    return app


def test_extend_schema_declares_a_plain_viewset_create_body():
    seen = {}

    class Plain(ViewSet):
        @extend_schema(request_schema=_CreatePost, response_schema=_PostOut)
        async def create(self, request):
            seen["model"] = self.get_action_request_model()
            seen["body"] = self.get_action_request_body()
            return {"id": "1", "title": seen["model"].title}

    app = _plain_app(Plain)
    operation = app.openapi()["paths"]["/posts"]["post"]
    ref = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("_CreatePost")
    assert "201" in operation["responses"]

    client = TestClient(app)
    created = client.post("/posts", json={"title": "hello"})
    assert created.status_code == 201
    assert isinstance(seen["model"], _CreatePost)
    assert seen["body"] == {"title": "hello"}

    bad = client.post("/posts", json={})
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "VALIDATION_ERROR"


def test_extend_schema_status_and_responses_on_destroy():
    from zeeb_api.exceptions import error_response_doc

    class Plain(ViewSet):
        lookup_field = "id"

        @extend_schema(
            status_code=202,
            responses={409: error_response_doc(409, "Busy", code="POST_BUSY")},
        )
        async def destroy(self, request, id: str):
            return {"accepted": True}

    responses = _plain_app(Plain).openapi()["paths"]["/posts/{id}"]["delete"]["responses"]
    assert "202" in responses and "204" not in responses
    assert responses["409"]["content"]["application/json"]["example"]["error"]["code"] == "POST_BUSY"


def test_action_status_code_and_responses():
    from zeeb_api.exceptions import error_response_doc

    class Plain(ViewSet):
        @action(
            detail=False,
            methods=["post"],
            request_schema=_CreatePost,
            status_code=202,
            responses={409: error_response_doc(409, "Already queued", code="ALREADY_QUEUED")},
        )
        async def enqueue(self, request):
            return {"queued": self.get_action_request_model().title}

    app = _plain_app(Plain)
    responses = app.openapi()["paths"]["/posts/enqueue"]["post"]["responses"]
    assert "202" in responses and "200" not in responses
    assert responses["409"]["description"] == "Already queued"
    assert responses["409"]["content"]["application/json"]["example"]["error"]["code"] == (
        "ALREADY_QUEUED"
    )
    assert {"400", "422"} <= set(responses)

    answered = TestClient(app).post("/posts/enqueue", json={"title": "x"})
    assert answered.status_code == 202
    assert answered.json() == {"queued": "x"}


class _SuspendOptions(BaseModel):
    billing_action: str | None = None


def test_an_all_optional_body_may_be_left_out():
    """Declaring a body whose fields are all optional must not turn a body-less
    POST - which a view reading the request itself accepted - into a 422."""
    seen = []

    class Plain(ViewSet):
        @action(detail=True, methods=["post"], request_schema=_SuspendOptions)
        async def suspend(self, request, pk=None):
            model = self.get_action_request_model()
            seen.append((model.model_fields_set, self.get_action_request_body()))
            return {"ok": True}

        @action(detail=False, methods=["post"], request_schema=_CreatePost)
        async def publish(self, request):
            return {"ok": True}

    app = _plain_app(Plain)
    client = TestClient(app)
    target = "/posts/3fa85f64-5717-4562-b3fc-2c963f66afa6/suspend"
    assert client.post(target).status_code == 200
    assert client.post(target, json={"billing_action": "none"}).status_code == 200
    assert seen == [(set(), {"billing_action": None}), ({"billing_action"}, {"billing_action": "none"})]

    spec = app.openapi()
    assert spec["paths"]["/posts/{id}/suspend"]["post"]["requestBody"].get("required") is not True
    assert spec["paths"]["/posts/publish"]["post"]["requestBody"]["required"] is True
    assert client.post("/posts/publish").status_code == 422


def test_extend_schema_documents_a_destroy_body_and_withdraws_a_response():
    class Plain(ViewSet):
        lookup_field = "id"

        @extend_schema(response_schema=_PostOut, status_code=202)
        async def destroy(self, request, id: str):
            return {"id": id, "title": "gone"}

        @extend_schema(request_schema=_CreatePost, responses={409: None})
        async def create(self, request):
            return {"id": "1", "title": self.get_action_request_model().title}

    paths = _plain_app(Plain).openapi()["paths"]
    accepted = paths["/posts/{id}"]["delete"]["responses"]["202"]
    assert accepted["content"]["application/json"]["schema"]["$ref"].endswith("_PostOut")
    assert "409" not in paths["/posts"]["post"]["responses"]
