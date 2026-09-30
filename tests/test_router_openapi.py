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

from zeeb_api.conf import settings
from zeeb_api.exception_handlers import install_error_response_schema, install_exception_handlers
from zeeb_api.pagination import LimitOffsetPagination
from zeeb_api.permissions import AllowAny, IsAuthenticated
from zeeb_api.routers.default import SimpleRouter
from zeeb_api.serializers import ModelSerializer
from zeeb_api.viewsets import ModelViewSet, action
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
