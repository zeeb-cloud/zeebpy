"""A route decides who the caller is before it looks at what they sent.

Authentication, permissions and throttling ran as the first lines of the
endpoint — after FastAPI had already validated the typed body — so an
anonymous POST with a bad body was answered 422, and a stranger could probe a
protected resource's schema one field at a time. They now run as a route
dependency, which FastAPI resolves before the body.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from zeeb_api.exception_handlers import install_exception_handlers
from zeeb_api.permissions import BasePermission
from zeeb_api.routers.default import SimpleRouter
from zeeb_api.serializers import ModelSerializer
from zeeb_api.viewsets import ModelViewSet
from zeeb_orm import Model, fields


class GatePost(Model):
    title = fields.CharField(max_length=100)

    class Meta:
        table_name = "gate_posts"


class GatePostSerializer(ModelSerializer):
    class Meta:
        model = GatePost
        fields = ["id", "title"]


class HeaderGate(BasePermission):
    """Admits a caller who sends ``X-Admitted``; a stand-in for a login.

    Anyone else is anonymous to the viewset, which answers 401 (not 403):
    the stranger is told to log in, never what the body should have held.
    """

    async def has_permission(self, request, view) -> bool:
        return "x-admitted" in request.headers


class Gated(ModelViewSet):
    queryset = GatePost.objects
    serializer_class = GatePostSerializer
    permission_classes = [HeaderGate]
    throttle_classes = []


def _client() -> TestClient:
    router = SimpleRouter()
    router.register("posts", Gated)
    app = FastAPI()
    install_exception_handlers(app)
    for api_router in router.get_urls():
        app.include_router(api_router)
    return TestClient(app)


def test_a_stranger_with_a_bad_body_is_refused_not_corrected():
    response = _client().post("/posts", json={})
    assert response.status_code == 401, response.text
    assert "title" not in response.text, "the body was inspected before the caller was"


def test_an_admitted_caller_with_a_bad_body_gets_the_validation_error():
    response = _client().post("/posts", json={}, headers={"X-Admitted": "1"})
    assert response.status_code in (400, 422), response.text
    assert "title" in response.text


def test_the_detail_route_is_guarded_the_same_way():
    response = _client().patch("/posts/1", json={"title": 5})
    assert response.status_code == 401, response.text
