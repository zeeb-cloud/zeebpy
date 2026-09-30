"""Tests for DRF-style throttling (zeeb_api.throttling)."""

import copy

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

import zeeb_api.throttling.base as throttling_base
from zeeb_api.conf import settings
from zeeb_api.exception_handlers import install_exception_handlers
from zeeb_api.exceptions import ImproperlyConfigured, RateLimitException
from zeeb_api.routers.default import SimpleRouter
from zeeb_api.throttling import (
    AnonRateThrottle,
    InMemoryThrottleCache,
    ScopedRateThrottle,
    SimpleRateThrottle,
    UserRateThrottle,
    set_throttle_cache,
    throttle,
)
from zeeb_api.viewsets import ViewSet, action

THROTTLE_SETTINGS = (
    "DEFAULT_THROTTLE_CLASSES",
    "DEFAULT_THROTTLE_RATES",
    "THROTTLE_NUM_PROXIES",
)


@pytest.fixture(autouse=True)
def reset_throttle_state():
    """Fresh cache per test; save/restore patched throttle settings."""
    saved = {name: copy.deepcopy(getattr(settings, name)) for name in THROTTLE_SETTINGS}
    set_throttle_cache(InMemoryThrottleCache())
    throttling_base._default_throttle_classes_cache = None

    yield

    for name, value in saved.items():
        setattr(settings, name, value)
    set_throttle_cache(InMemoryThrottleCache())
    throttling_base._default_throttle_classes_cache = None


def make_request(headers=None, client=("10.0.0.1", 1234)):
    raw_headers = [
        (key.lower().encode(), value.encode()) for key, value in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": raw_headers,
        "client": client,
        "scheme": "http",
        "server": ("testserver", 80),
    }
    return Request(scope)


class TwoPerMinuteThrottle(SimpleRateThrottle):
    scope = "test"
    rate = "2/min"

    def get_cache_key(self, request, view):
        return self.cache_format.format(scope=self.scope, ident=self.get_ident(request))


class TestParseRate:
    @pytest.mark.parametrize(
        "rate,expected",
        [
            ("100/min", (100, 60)),
            ("10/m", (10, 60)),
            ("5/s", (5, 1)),
            ("5/sec", (5, 1)),
            ("7/hour", (7, 3600)),
            ("7/h", (7, 3600)),
            ("3/day", (3, 86400)),
            ("3/d", (3, 86400)),
            (None, (None, None)),
        ],
    )
    def test_parse_rate(self, rate, expected):
        throttle_instance = TwoPerMinuteThrottle()
        assert throttle_instance.parse_rate(rate) == expected

    @pytest.mark.parametrize("rate", ["invalid", "10/xyz", "x/min", "10"])
    def test_parse_rate_invalid(self, rate):
        throttle_instance = TwoPerMinuteThrottle()
        with pytest.raises(ImproperlyConfigured):
            throttle_instance.parse_rate(rate)


class TestSlidingWindow:
    """Sliding-window math with an injected fake clock."""

    def _make_throttle(self, clock):
        instance = TwoPerMinuteThrottle()
        instance.timer = lambda: clock["now"]
        return instance

    async def test_allows_until_limit_then_denies(self):
        clock = {"now": 0.0}
        request = make_request()

        assert await self._make_throttle(clock).allow_request(request, None) is True
        clock["now"] = 1.0
        assert await self._make_throttle(clock).allow_request(request, None) is True
        clock["now"] = 2.0
        assert await self._make_throttle(clock).allow_request(request, None) is False

    async def test_window_slides_and_frees_slots(self):
        clock = {"now": 0.0}
        request = make_request()

        await self._make_throttle(clock).allow_request(request, None)
        clock["now"] = 1.0
        await self._make_throttle(clock).allow_request(request, None)

        # Still inside the window: denied
        clock["now"] = 59.0
        assert await self._make_throttle(clock).allow_request(request, None) is False

        # Both timestamps (0.0, 1.0) fall out of the 60s window at t=61
        clock["now"] = 61.0
        assert await self._make_throttle(clock).allow_request(request, None) is True

    async def test_wait_returns_time_until_slot_frees(self):
        clock = {"now": 0.0}
        request = make_request()

        await self._make_throttle(clock).allow_request(request, None)
        clock["now"] = 2.0
        await self._make_throttle(clock).allow_request(request, None)

        clock["now"] = 10.0
        denied = self._make_throttle(clock)
        assert await denied.allow_request(request, None) is False
        # Oldest entry at t=0 frees its slot at t=60 -> wait 50s
        assert denied.wait() == pytest.approx(50.0)

    async def test_separate_idents_have_separate_windows(self):
        clock = {"now": 0.0}
        request_a = make_request(client=("10.0.0.1", 1))
        request_b = make_request(client=("10.0.0.2", 1))

        await self._make_throttle(clock).allow_request(request_a, None)
        await self._make_throttle(clock).allow_request(request_a, None)
        assert await self._make_throttle(clock).allow_request(request_a, None) is False
        assert await self._make_throttle(clock).allow_request(request_b, None) is True


class TestGetIdent:
    def test_uses_client_host_by_default(self):
        request = make_request(headers={"X-Forwarded-For": "1.2.3.4, 5.6.7.8"})
        assert TwoPerMinuteThrottle().get_ident(request) == "10.0.0.1"

    def test_uses_forwarded_for_hop_from_the_right_with_proxies(self):
        # With 1 trusted proxy, the real client is the LAST XFF entry (the hop
        # our proxy actually observed), not the attacker-controlled first entry.
        settings.THROTTLE_NUM_PROXIES = 1
        request = make_request(headers={"X-Forwarded-For": "1.2.3.4, 5.6.7.8"})
        assert TwoPerMinuteThrottle().get_ident(request) == "5.6.7.8"

    def test_forwarded_for_counts_from_the_right_for_multiple_proxies(self):
        settings.THROTTLE_NUM_PROXIES = 2
        request = make_request(
            headers={"X-Forwarded-For": "1.1.1.1, 2.2.2.2, 3.3.3.3"}
        )
        assert TwoPerMinuteThrottle().get_ident(request) == "2.2.2.2"

    def test_spoofed_leftmost_forwarded_for_does_not_change_ident(self):
        # An attacker prepending a forged address cannot mint a new throttle key:
        # the identity is pinned to the hop the trusted proxy saw (the last one).
        settings.THROTTLE_NUM_PROXIES = 1
        forged = make_request(
            headers={"X-Forwarded-For": "9.9.9.9, 5.6.7.8"}
        )
        legit = make_request(headers={"X-Forwarded-For": "5.6.7.8"})
        assert (
            TwoPerMinuteThrottle().get_ident(forged)
            == TwoPerMinuteThrottle().get_ident(legit)
            == "5.6.7.8"
        )

    def test_short_chain_is_clamped_not_indexerror(self):
        # More trusted proxies configured than present hops: fall back to the
        # left-most present hop instead of raising.
        settings.THROTTLE_NUM_PROXIES = 3
        request = make_request(headers={"X-Forwarded-For": "1.2.3.4"})
        assert TwoPerMinuteThrottle().get_ident(request) == "1.2.3.4"


class TestAnonRateThrottle:
    async def test_unset_rate_is_noop(self):
        # Default settings: DEFAULT_THROTTLE_RATES["anon"] is None
        instance = AnonRateThrottle()
        request = make_request()
        for _ in range(10):
            assert await instance.allow_request(request, None) is True

    async def test_skips_authenticated_requests(self):
        settings.DEFAULT_THROTTLE_RATES = {"anon": "1/min"}
        request = make_request()
        request.state.user = object()

        instance = AnonRateThrottle()
        assert instance.get_cache_key(request, None) is None
        for _ in range(3):
            assert await instance.allow_request(request, None) is True

    async def test_throttles_anonymous_requests(self):
        settings.DEFAULT_THROTTLE_RATES = {"anon": "1/min"}
        request = make_request()

        assert await AnonRateThrottle().allow_request(request, None) is True
        assert await AnonRateThrottle().allow_request(request, None) is False


class TestUserRateThrottle:
    async def test_keys_by_user_id(self):
        settings.DEFAULT_THROTTLE_RATES = {"user": "1/min"}

        class FakeUser:
            def __init__(self, user_id):
                self.id = user_id

        request_a = make_request()
        request_a.state.user = FakeUser("a")
        request_b = make_request()
        request_b.state.user = FakeUser("b")

        assert await UserRateThrottle().allow_request(request_a, None) is True
        assert await UserRateThrottle().allow_request(request_a, None) is False
        assert await UserRateThrottle().allow_request(request_b, None) is True

    async def test_falls_back_to_ip_for_anonymous(self):
        settings.DEFAULT_THROTTLE_RATES = {"user": "1/min"}
        request = make_request()
        instance = UserRateThrottle()
        assert instance.get_cache_key(request, None) == "throttle:user:10.0.0.1"


class TestScopedRateThrottle:
    async def test_reads_view_throttle_scope(self):
        settings.DEFAULT_THROTTLE_RATES = {"uploads": "1/min"}

        class UploadView:
            throttle_scope = "uploads"

        view = UploadView()
        request = make_request()

        assert await ScopedRateThrottle().allow_request(request, view) is True
        assert await ScopedRateThrottle().allow_request(request, view) is False

    async def test_view_without_scope_is_not_throttled(self):
        settings.DEFAULT_THROTTLE_RATES = {"uploads": "1/min"}

        class PlainView:
            pass

        instance = ScopedRateThrottle()
        request = make_request()
        for _ in range(3):
            assert await instance.allow_request(request, PlainView()) is True


class ThrottledViewSet(ViewSet):
    throttle_classes = [AnonRateThrottle]

    @action(detail=False, methods=["get"])
    async def ping(self, request):
        return {"ok": True}


class DefaultThrottledViewSet(ViewSet):
    # throttle_classes = None -> falls back to settings.DEFAULT_THROTTLE_CLASSES
    @action(detail=False, methods=["get"])
    async def ping(self, request):
        return {"ok": True}


def _make_app(viewset_class) -> FastAPI:
    app = FastAPI()
    install_exception_handlers(app)
    router = SimpleRouter()
    router.register("things", viewset_class)
    for api_router in router.get_urls():
        app.include_router(api_router)
    return app


class TestViewSetIntegration:
    async def test_third_request_throttled_with_envelope(self):
        settings.DEFAULT_THROTTLE_RATES = {"anon": "2/min"}
        app = _make_app(ThrottledViewSet)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/things/ping")).status_code == 200
            assert (await client.get("/things/ping")).status_code == 200

            response = await client.get("/things/ping")
            assert response.status_code == 429
            assert "Retry-After" in response.headers
            assert int(response.headers["Retry-After"]) > 0
            body = response.json()
            assert body["success"] is False
            assert body["error"]["code"] == "RATE_LIMIT_EXCEEDED"

    async def test_default_throttle_classes_from_settings(self):
        settings.DEFAULT_THROTTLE_CLASSES = ["zeeb_api.throttling.AnonRateThrottle"]
        settings.DEFAULT_THROTTLE_RATES = {"anon": "1/min"}
        app = _make_app(DefaultThrottledViewSet)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/things/ping")).status_code == 200
            assert (await client.get("/things/ping")).status_code == 429

    async def test_no_throttle_classes_no_limit(self):
        # Defaults: throttle_classes None + DEFAULT_THROTTLE_CLASSES []
        app = _make_app(DefaultThrottledViewSet)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            for _ in range(5):
                assert (await client.get("/things/ping")).status_code == 200


class TestThrottleDependency:
    async def test_plain_route_throttled(self):
        app = FastAPI()
        install_exception_handlers(app)

        @app.get("/login", dependencies=[Depends(throttle("2/min", scope="login-test"))])
        async def login():
            return {"ok": True}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/login")).status_code == 200
            assert (await client.get("/login")).status_code == 200

            response = await client.get("/login")
            assert response.status_code == 429
            assert "Retry-After" in response.headers
            assert response.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"


class TestCheckThrottles:
    async def test_check_throttles_raises_rate_limit_exception(self):
        settings.DEFAULT_THROTTLE_RATES = {"anon": "1/min"}
        request = make_request()

        viewset = ThrottledViewSet(request=request)
        await viewset.check_throttles(request)

        with pytest.raises(RateLimitException) as exc_info:
            await ThrottledViewSet(request=request).check_throttles(request)
        assert exc_info.value.status_code == 429
        assert "Retry-After" in (exc_info.value.headers or {})


# --------------------------------------------------------------------------- #
# Atomic check-and-record; /auth/refresh and the OAuth routes are throttled
# --------------------------------------------------------------------------- #


class _YieldingCache(InMemoryThrottleCache):
    """A cache whose reads suspend, like any networked backend's would."""

    async def get_history(self, key):
        import asyncio

        history = await super().get_history(key)
        await asyncio.sleep(0)  # the round trip back from the backend
        return history


async def test_concurrent_requests_cannot_burst_past_the_limit():
    import asyncio

    class FivePerMinute(TwoPerMinuteThrottle):
        rate = "5/min"

    set_throttle_cache(_YieldingCache())
    request = make_request()
    results = await asyncio.gather(
        *(FivePerMinute().allow_request(request, None) for _ in range(20))
    )
    assert results.count(True) == 5


async def test_default_check_and_record_serves_custom_caches():
    """A cache implementing only get/set_history keeps working."""
    from zeeb_api.throttling import BaseThrottleCache

    class DictCache(BaseThrottleCache):
        def __init__(self):
            self.data = {}

        async def get_history(self, key):
            return list(self.data.get(key, []))

        async def set_history(self, key, history, duration):
            self.data[key] = list(history)

    set_throttle_cache(DictCache())
    request = make_request()
    assert await TwoPerMinuteThrottle().allow_request(request, None) is True
    assert await TwoPerMinuteThrottle().allow_request(request, None) is True
    denied = TwoPerMinuteThrottle()
    assert await denied.allow_request(request, None) is False
    assert denied.wait() is not None


@pytest.fixture
def jwt_secret():
    import zeeb_api.auth.jwt as jwt_module
    from zeeb_api.auth.jwt import configure_jwt

    saved = jwt_module._jwt_config
    configure_jwt(secret_key="a-real-strong-secret-of-at-least-32-bytes")
    yield
    jwt_module._jwt_config = saved


def _auth_app(**router_kwargs) -> FastAPI:
    from zeeb_api.auth.router import create_auth_router

    async def authenticate(request, body):
        return None  # every login fails; the throttle is what is under test

    app = FastAPI()
    install_exception_handlers(app)
    app.include_router(
        create_auth_router(
            authenticate=authenticate,
            use_database=False,
            enable_registration=False,
            **router_kwargs,
        )
    )
    return app


async def _statuses(app, path, body, n):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return [(await client.post(path, json=body)).status_code for _ in range(n)]


async def test_refresh_is_throttled_like_login(jwt_secret):
    app = _auth_app(login_throttle="2/min")
    statuses = await _statuses(app, "/auth/refresh", {"refresh_token": "junk"}, 3)
    assert statuses == [401, 401, 429]
    # Its own bucket: the refresh attempts did not use up the login budget.
    login = await _statuses(app, "/auth/login", {"email": "a@b.co", "password": "x"}, 1)
    assert login == [401]


async def test_refresh_throttle_can_be_disabled(jwt_secret):
    app = _auth_app(login_throttle="2/min", refresh_throttle=None)
    statuses = await _statuses(app, "/auth/refresh", {"refresh_token": "junk"}, 3)
    assert statuses == [401, 401, 401]


def _oauth_app(**router_kwargs) -> FastAPI:
    from zeeb_api.auth.oauth import OAuthProvider, create_oauth_router

    provider = OAuthProvider(
        name="test",
        client_id="client",
        authorization_endpoint="https://idp.example/authorize",
        token_endpoint="https://idp.example/token",
    )
    app = FastAPI()
    install_exception_handlers(app)
    app.include_router(create_oauth_router(providers={"test": provider}, **router_kwargs))
    return app


async def test_oauth_routes_are_throttled(jwt_secret):
    app = _oauth_app(throttle_rate="2/min")
    body = {"code": "c", "redirect_uri": "https://spa.example/cb", "state": "junk"}
    statuses = await _statuses(app, "/auth/test/token/", body, 3)
    assert statuses == [401, 401, 429]


async def test_oauth_throttle_defaults_to_the_login_rate_setting(jwt_secret):
    saved = getattr(settings, "AUTH_LOGIN_THROTTLE_RATE", None)
    settings.AUTH_LOGIN_THROTTLE_RATE = "1/min"
    try:
        app = _oauth_app()
    finally:
        settings.AUTH_LOGIN_THROTTLE_RATE = saved
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.get("/auth/test/state/")
        second = await client.get("/auth/test/state/")
    assert first.status_code == 200, first.text
    assert second.status_code == 429
