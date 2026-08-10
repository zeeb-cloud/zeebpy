"""Templates for the ``accounts`` app that ``zeeb startproject`` generates.

Unlike the generic ``startapp`` templates these carry real content: a project
owns its user model from the first migration, because swapping it afterwards
means rewriting every foreign key that already points at the built-in one.

The app ships no ``tests.py`` — project tests live in ``tests/``, where the
shared fixtures from :mod:`zeeb_orm.scaffold.harness` apply. The assertion that
``AUTH_USER_MODEL`` really resolves here is part of ``tests/test_smoke.py``.
"""

MODELS_PY = '''"""accounts models — this project's user model.

``AUTH_USER_MODEL = "accounts.User"`` in settings.py points the whole framework
here: ``get_user_model()``, the auth endpoints, ``createsuperuser`` and every
foreign key to a user resolve to this class. Add project-specific fields
directly to ``User`` — ``AbstractUser`` already provides ``email`` (the login
field), ``username``, ``first_name``/``last_name``, ``is_active``/``is_staff``/
``is_superuser``, ``date_joined`` and password hashing.

Reference this model by its labelled name, ``ForeignKey("accounts.User")``,
rather than the bare ``"User"``: the framework's own user class shares that
class name, so the bare form depends on import order.
"""

from zeeb_api.auth.models import AbstractUser

# Registering the OAuth identity table from here keeps the schema identical in
# every environment: it lands in the initial migration whether or not an OAuth
# provider is configured, so enabling one later needs no migration. Importing
# this module pulls in no HTTP client — httpx is only needed at request time.
from zeeb_api.auth.oauth.models import ExternalIdentity  # noqa: F401
from zeeb_orm import Model, fields  # noqa: F401  (ready for your own models)


class User(AbstractUser):
    """The project user. Extend with your own fields."""

    class Meta:
        # ``Meta`` is not inherited, so the table name has to be stated here.
        table_name = "accounts_user"
'''

SERIALIZERS_PY = '''"""accounts serializers."""

from zeeb_api import serializers

from .models import User


class UserSerializer(serializers.ModelSerializer):
    """Public representation of a user. The password hash is never exposed."""

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "username",
            "first_name",
            "last_name",
            "is_active",
            "is_staff",
            "date_joined",
        ]
        read_only_fields = ["id", "is_active", "is_staff", "date_joined"]
'''

VIEWS_PY = '''"""accounts views."""

from zeeb_api import permissions, viewsets

from .models import User
from .serializers import UserSerializer


class UserViewSet(viewsets.ModelViewSet):
    """User administration.

    Staff-only by default. Self-service needs no elevated permission and is
    already covered by the auth endpoints: GET <auth prefix>/me and POST
    <auth prefix>/register. Relax ``permission_classes`` here if your product
    exposes a user directory.
    """

    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAdminUser]
'''

URLS_PY = '''"""accounts URL configuration."""

from zeeb_api.routers import DefaultRouter

from .views import UserViewSet

router = DefaultRouter()
router.register("users", UserViewSet)
'''
