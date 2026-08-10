"""accounts tests."""

import pytest

from .models import User


async def test_user_model_is_the_projects_own():
    """AUTH_USER_MODEL points here, so this is the class the API issues."""
    from zeeb_api.auth.backends import get_user_model

    assert get_user_model() is User
    assert User._meta.db_table == "accounts_user"
