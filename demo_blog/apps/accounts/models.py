"""accounts models — this project's user model.

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

from zeeb_orm import Model, fields  # noqa: F401  (ready for your own models)

from zeeb_api.auth.models import AbstractUser

# Registering the OAuth identity table from here keeps the schema identical in
# every environment: it lands in the initial migration whether or not an OAuth
# provider is configured, so enabling one later needs no migration. Importing
# this module pulls in no HTTP client — httpx is only needed at request time.
from zeeb_api.auth.oauth.models import ExternalIdentity  # noqa: F401


class User(AbstractUser):
    """The project user. Extend with your own fields."""

    class Meta:
        # ``Meta`` is not inherited, so the table name has to be stated here.
        table_name = "accounts_user"
