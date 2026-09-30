"""Login does equal bcrypt work for every account state; passwords fit bcrypt.

``authenticate()`` returned before any bcrypt work when the email was unknown
(or the account inactive), so response time told an attacker which emails are
registered. And bcrypt >= 5 raises ``ValueError`` for input over 72 bytes,
while ``RegisterRequest`` had no upper bound: a long password made
``/auth/register`` answer 500.
"""

import bcrypt
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from zeeb_api.auth.jwt import configure_jwt
from zeeb_api.auth.router import create_auth_router
from zeeb_api.exception_handlers import install_exception_handlers
from zeeb_api.exceptions import PasswordTooLongError

SECRET = "a-real-strong-secret-of-at-least-32-bytes"


@pytest.fixture(autouse=True)
def jwt_secret():
    import zeeb_api.auth.jwt as jwt_module

    saved = jwt_module._jwt_config
    configure_jwt(secret_key=SECRET)
    yield
    jwt_module._jwt_config = saved


def _models():
    from zeeb_api.auth.models import Permission, User, UserPermission
    from zeeb_api.auth.oauth.models import ExternalIdentity

    return (User, Permission, UserPermission, ExternalIdentity)


@pytest.fixture
async def db():
    from zeeb_orm import close_all_connections, configure, setup_database
    from zeeb_orm.conf.settings import Settings
    from zeeb_orm.models.base import metadata

    models = _models()
    Settings.reset()
    for model in models:
        model._sa_table = None
        model._sa_model = None
    metadata.clear()

    configure(database={"url": "sqlite+aiosqlite:///:memory:"})
    database = await setup_database("sqlite+aiosqlite:///:memory:")
    for model in models:
        model._get_table()
    await database.create_all()
    yield database
    await database.drop_all()
    await close_all_connections()
    for model in models:
        table = metadata.tables.get(model._meta.db_table)
        if table is not None:
            metadata.remove(table)
        model._sa_table = None
        model._sa_model = None
    Settings.reset()


def _client() -> AsyncClient:
    app = FastAPI()
    install_exception_handlers(app)
    app.include_router(create_auth_router())
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# --------------------------------------------------------------------------- #
# Timing: every login attempt runs exactly one bcrypt verification
# --------------------------------------------------------------------------- #


@pytest.fixture
def checkpw_calls(monkeypatch):
    calls: list[bytes] = []
    real = bcrypt.checkpw

    def counting(password, hashed):
        calls.append(password)
        return real(password, hashed)

    monkeypatch.setattr(bcrypt, "checkpw", counting)
    return calls


async def test_unknown_email_costs_one_bcrypt_check(db, checkpw_calls):
    from zeeb_api.auth.backends import authenticate

    assert await authenticate(email="nobody@example.com", password="whatever-123") is None
    assert len(checkpw_calls) == 1


async def test_inactive_account_costs_one_bcrypt_check(db, checkpw_calls):
    from zeeb_api.auth.backends import authenticate, create_user

    await create_user(email="off@example.com", password="right-pass-1", is_active=False)
    checkpw_calls.clear()
    assert await authenticate(email="off@example.com", password="right-pass-1") is None
    assert len(checkpw_calls) == 1


async def test_existing_account_costs_one_bcrypt_check(db, checkpw_calls):
    from zeeb_api.auth.backends import authenticate, create_user

    await create_user(email="on@example.com", password="right-pass-1")
    checkpw_calls.clear()
    assert await authenticate(email="on@example.com", password="wrong-pass-1") is None
    assert await authenticate(email="on@example.com", password="right-pass-1") is not None
    assert len(checkpw_calls) == 2


# --------------------------------------------------------------------------- #
# bcrypt's 72-byte limit is a field error, never a 500
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("password", ["p" * 73, "ä" * 37])  # 73 and 74 bytes
async def test_register_refuses_over_72_bytes_with_field_error(db, password):
    async with _client() as client:
        response = await client.post(
            "/auth/register", json={"email": "long@example.com", "password": password}
        )
    assert response.status_code == 422, response.text
    detail = response.json()["error"]["details"][0]
    assert detail["field"] == "password"
    assert detail["code"] == "FIELD_TOO_LONG"
    assert detail["meta"]["max_length"] == 72


async def test_register_accepts_exactly_72_bytes(db):
    async with _client() as client:
        response = await client.post(
            "/auth/register", json={"email": "edge@example.com", "password": "ä" * 36}
        )
        login = await client.post(
            "/auth/login", json={"email": "edge@example.com", "password": "ä" * 36}
        )
    assert response.status_code == 200, response.text
    assert login.status_code == 200, login.text


async def test_login_refuses_over_72_bytes_with_field_error(db):
    async with _client() as client:
        response = await client.post(
            "/auth/login", json={"email": "any@example.com", "password": "p" * 100}
        )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"][0]["field"] == "password"


def test_set_password_over_72_bytes_is_a_field_error():
    from zeeb_api.auth.models import User

    with pytest.raises(PasswordTooLongError) as caught:
        User(email="x@example.com").set_password("p" * 73)
    error = caught.value
    assert isinstance(error, ValueError)  # code setting passwords keeps catching this
    assert error.status_code == 400
    assert error.details[0].field == "password"
    assert error.details[0].code == "FIELD_TOO_LONG"


def test_check_password_over_72_bytes_is_false_not_an_error():
    from zeeb_api.auth.hashers import check_password, make_password

    hashed = make_password("p" * 72)
    assert check_password("p" * 73, hashed) is False
