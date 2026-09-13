"""accounts serializers."""

from zeeb_api import serializers

from .models import User


class UserSerializer(serializers.ModelSerializer):
    """Public representation of a user. The password hash is never exposed."""

    class Meta:
        model = User
        fields = ["id", "email", "username", "first_name", "last_name", "is_active", "is_staff", "date_joined"]
        read_only_fields = ["id", "is_active", "is_staff", "date_joined"]
