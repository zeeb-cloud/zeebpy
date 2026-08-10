"""accounts views."""

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
