"""Tests for zeeb_api components."""

import pytest
from zeeb_api.serializers import (
    Serializer, ModelSerializer,
    CharField, IntegerField, EmailField, BooleanField,
)
from zeeb_api.serializers.fields import empty
from zeeb_api.response import ValidationError


class TestSerializerFields:
    """Test serializer field validation."""
    
    def test_char_field_valid(self):
        field = CharField()
        field.bind("name", None)
        assert field.run_validation("hello") == "hello"
    
    def test_char_field_max_length(self):
        field = CharField(max_length=5)
        field.bind("name", None)
        with pytest.raises(ValidationError):
            field.run_validation("too long string")
    
    def test_char_field_blank(self):
        field = CharField(allow_blank=False)
        field.bind("name", None)
        with pytest.raises(ValidationError):
            field.run_validation("")
    
    def test_integer_field_valid(self):
        field = IntegerField()
        field.bind("age", None)
        assert field.run_validation("42") == 42
        assert field.run_validation(42) == 42
    
    def test_integer_field_invalid(self):
        field = IntegerField()
        field.bind("age", None)
        with pytest.raises(ValidationError):
            field.run_validation("not a number")
    
    def test_integer_field_min_max(self):
        field = IntegerField(min_value=0, max_value=100)
        field.bind("score", None)
        assert field.run_validation(50) == 50
        
        with pytest.raises(ValidationError):
            field.run_validation(-1)
        
        with pytest.raises(ValidationError):
            field.run_validation(101)
    
    def test_email_field_valid(self):
        field = EmailField()
        field.bind("email", None)
        assert field.run_validation("test@example.com") == "test@example.com"
    
    def test_email_field_invalid(self):
        field = EmailField()
        field.bind("email", None)
        with pytest.raises(ValidationError):
            field.run_validation("not an email")
    
    def test_boolean_field(self):
        field = BooleanField()
        field.bind("active", None)
        assert field.run_validation(True) is True
        assert field.run_validation("true") is True
        assert field.run_validation("1") is True
        assert field.run_validation(False) is False
        assert field.run_validation("false") is False
        assert field.run_validation("0") is False


class TestSerializer:
    """Test base Serializer class."""
    
    def test_serializer_validation(self):
        class UserSerializer(Serializer):
            name = CharField(max_length=100)
            email = EmailField()
            age = IntegerField(required=False)
        
        # Valid data
        serializer = UserSerializer(data={
            "name": "Alice",
            "email": "alice@example.com",
        })
        assert serializer.is_valid() is True
        assert serializer.validated_data["name"] == "Alice"
        assert serializer.validated_data["email"] == "alice@example.com"
    
    def test_serializer_invalid_data(self):
        class UserSerializer(Serializer):
            name = CharField(max_length=100)
            email = EmailField()
        
        serializer = UserSerializer(data={
            "name": "Alice",
            "email": "not-an-email",
        })
        assert serializer.is_valid() is False
        assert "email" in serializer.errors
    
    def test_serializer_required_field(self):
        class UserSerializer(Serializer):
            name = CharField()
            email = EmailField()
        
        serializer = UserSerializer(data={"name": "Alice"})
        assert serializer.is_valid() is False
        assert "email" in serializer.errors
    
    def test_serializer_output(self):
        class UserSerializer(Serializer):
            name = CharField()
            email = EmailField()
        
        class FakeUser:
            name = "Bob"
            email = "bob@example.com"
        
        serializer = UserSerializer(instance=FakeUser())
        assert serializer.data == {"name": "Bob", "email": "bob@example.com"}
    
    def test_serializer_read_only(self):
        class UserSerializer(Serializer):
            id = IntegerField(read_only=True)
            name = CharField()
        
        serializer = UserSerializer(data={"id": 999, "name": "Alice"})
        assert serializer.is_valid() is True
        # read_only fields should not be in validated_data
        assert "id" not in serializer.validated_data
    
    def test_serializer_write_only(self):
        class UserSerializer(Serializer):
            name = CharField()
            password = CharField(write_only=True)
        
        class FakeUser:
            name = "Alice"
            password = "secret"
        
        serializer = UserSerializer(instance=FakeUser())
        # write_only fields should not be in output
        assert "password" not in serializer.data
        assert serializer.data == {"name": "Alice"}


class TestViewSet:
    """Test ViewSet classes."""
    
    def test_action_decorator(self):
        from zeeb_api.viewsets import action, ModelViewSet
        
        class TestViewSet(ModelViewSet):
            @action(detail=True, methods=["post"])
            async def activate(self, request, pk=None):
                return {"status": "activated"}
            
            @action(detail=False, methods=["get"])
            async def recent(self, request):
                return {"items": []}
        
        # Check action config is attached
        assert hasattr(TestViewSet.activate, "_action_config")
        config = TestViewSet.activate._action_config
        assert config["detail"] is True
        assert config["methods"] == ["POST"]
        
        assert hasattr(TestViewSet.recent, "_action_config")
        config = TestViewSet.recent._action_config
        assert config["detail"] is False


class TestRouter:
    """Test Router classes."""
    
    def test_router_registration(self):
        from zeeb_api.routers import DefaultRouter
        from zeeb_api.viewsets import ModelViewSet
        
        class DummyViewSet(ModelViewSet):
            pass
        
        router = DefaultRouter()
        router.register("items", DummyViewSet, basename="item")
        
        assert len(router._registry) == 1
        prefix, viewset, basename = router._registry[0]
        assert prefix == "items"
        assert viewset is DummyViewSet
        assert basename == "item"


class TestPermissions:
    """Test permission classes."""
    
    @pytest.mark.asyncio
    async def test_allow_any(self):
        from zeeb_api.permissions import AllowAny
        from unittest.mock import MagicMock
        
        permission = AllowAny()
        request = MagicMock()
        view = MagicMock()
        
        assert await permission.has_permission(request, view) is True
    
    @pytest.mark.asyncio
    async def test_is_authenticated_no_user(self):
        from zeeb_api.permissions import IsAuthenticated
        from unittest.mock import MagicMock
        
        permission = IsAuthenticated()
        request = MagicMock()
        request.state = MagicMock()
        request.state.user = None
        view = MagicMock()
        
        assert await permission.has_permission(request, view) is False
    
    @pytest.mark.asyncio
    async def test_is_authenticated_with_user(self):
        from zeeb_api.permissions import IsAuthenticated
        from unittest.mock import MagicMock
        
        permission = IsAuthenticated()
        request = MagicMock()
        request.state = MagicMock()
        request.state.user = MagicMock(is_authenticated=True)
        view = MagicMock()
        
        assert await permission.has_permission(request, view) is True


class TestPagination:
    """Test pagination classes."""
    
    def test_page_number_pagination_config(self):
        from zeeb_api.pagination import PageNumberPagination
        
        class CustomPagination(PageNumberPagination):
            page_size = 50
            max_page_size = 200
        
        paginator = CustomPagination()
        assert paginator.page_size == 50
        assert paginator.max_page_size == 200
    
    def test_limit_offset_pagination_config(self):
        from zeeb_api.pagination import LimitOffsetPagination
        
        class CustomPagination(LimitOffsetPagination):
            default_limit = 30
            max_limit = 150
        
        paginator = CustomPagination()
        assert paginator.default_limit == 30
        assert paginator.max_limit == 150
