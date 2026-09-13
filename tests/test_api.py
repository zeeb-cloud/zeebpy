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


class TestSerializerFieldConstraints:
    """B3: declared field constraints/validators must be enforced by the
    exported (pydantic-backed) Serializer, not silently dropped."""

    def test_max_length_enforced(self):
        class S(Serializer):
            name = CharField(max_length=5)

        assert S(data={"name": "abc"}).is_valid() is True
        bad = S(data={"name": "way-too-long"})
        assert bad.is_valid() is False
        assert "name" in bad.errors

    def test_min_length_enforced(self):
        class S(Serializer):
            name = CharField(min_length=3)

        assert S(data={"name": "abcd"}).is_valid() is True
        bad = S(data={"name": "ab"})
        assert bad.is_valid() is False
        assert "name" in bad.errors

    def test_allow_blank_false_enforced(self):
        class S(Serializer):
            name = CharField(allow_blank=False)

        bad = S(data={"name": ""})
        assert bad.is_valid() is False
        assert "name" in bad.errors

    def test_custom_validator_enforced(self):
        def no_spaces(value):
            if " " in value:
                raise ValueError("must not contain spaces")

        class S(Serializer):
            slug = CharField(validators=[no_spaces])

        assert S(data={"slug": "ok"}).is_valid() is True
        bad = S(data={"slug": "has space"})
        assert bad.is_valid() is False
        assert "slug" in bad.errors


class TestSerializerPartialValidation:
    """B2: PATCH (partial=True) must validate the fields the client sent
    instead of accepting them verbatim, while allowing omitted fields."""

    def test_partial_rejects_invalid_type(self):
        class S(Serializer):
            age = IntegerField()

        ser = S(data={"age": "not-a-number"}, partial=True)
        assert ser.is_valid() is False
        assert "age" in ser.errors

    def test_partial_allows_missing_fields(self):
        class S(Serializer):
            name = CharField()
            age = IntegerField()

        ser = S(data={"name": "Alice"}, partial=True)  # age omitted
        assert ser.is_valid() is True
        assert ser.validated_data == {"name": "Alice"}

    def test_partial_enforces_constraints_on_provided_fields(self):
        class S(Serializer):
            name = CharField(max_length=5)

        assert S(data={"name": "abc"}, partial=True).is_valid() is True
        bad = S(data={"name": "way-too-long"}, partial=True)
        assert bad.is_valid() is False
        assert "name" in bad.errors


class TestModelSerializerForeignKey:
    """Regression tests for FK serialization (ForeignKeyLazyLoader leak)."""

    def _models(self):
        import uuid
        from zeeb_orm import Model, fields
        from zeeb_orm.models.fields import ForeignKeyLazyLoader

        suffix = uuid.uuid4().hex[:8]

        class Project(Model):
            name = fields.CharField(max_length=100)

            class Meta:
                table_name = f"fk_projects_{suffix}"

        class Task(Model):
            title = fields.CharField(max_length=100)
            project = fields.ForeignKey(Project, related_name="tasks")

            class Meta:
                table_name = f"fk_tasks_{suffix}"

        class Profile(Model):
            bio = fields.TextField(default="")
            project = fields.OneToOneField(Project, related_name="profile")

            class Meta:
                table_name = f"fk_profiles_{suffix}"

        return Project, Task, Profile, ForeignKeyLazyLoader

    def test_foreignkey_serialized_as_pk_not_lazy_loader(self):
        import uuid

        Project, Task, _Profile, ForeignKeyLazyLoader = self._models()

        class TaskSerializer(ModelSerializer):
            class Meta:
                model = Task
                fields = "__all__"

        project_id = uuid.uuid4()
        task = Task(title="Write tests", project=project_id)

        # Sanity: the raw descriptor would leak a lazy loader.
        assert isinstance(task.project, ForeignKeyLazyLoader)

        serializer = TaskSerializer(instance=task)
        value = serializer.data["project"]
        assert not isinstance(value, ForeignKeyLazyLoader)
        assert value == project_id

        # And the serialized dict validates against the generated response schema.
        TaskSerializer.ResponseSchema(**serializer.data)

    def test_onetoone_serialized_as_pk_not_lazy_loader(self):
        import uuid

        Project, _Task, Profile, ForeignKeyLazyLoader = self._models()

        class ProfileSerializer(ModelSerializer):
            class Meta:
                model = Profile
                fields = "__all__"

        project_id = uuid.uuid4()
        profile = Profile(bio="hi", project=project_id)

        serializer = ProfileSerializer(instance=profile)
        value = serializer.data["project"]
        assert not isinstance(value, ForeignKeyLazyLoader)
        assert value == project_id


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


class TestCustomActionDecorator:
    """Test @action decorator with schemas and permissions."""
    
    def test_action_with_request_schema(self):
        from pydantic import BaseModel
        from zeeb_api.viewsets import action, ModelViewSet
        
        class SendEmailRequest(BaseModel):
            subject: str
            body: str
        
        class TestViewSet(ModelViewSet):
            @action(
                detail=False,
                methods=["post"],
                request_schema=SendEmailRequest,
            )
            async def send_email(self, request):
                return {"queued": True}
        
        config = TestViewSet.send_email._action_config
        assert config["request_schema"] is SendEmailRequest
        assert config["response_schema"] is None
    
    def test_action_with_response_schema(self):
        from pydantic import BaseModel
        from zeeb_api.viewsets import action, ModelViewSet
        
        class SendEmailResponse(BaseModel):
            queued: bool
            job_id: str
        
        class TestViewSet(ModelViewSet):
            @action(
                detail=False,
                methods=["post"],
                response_schema=SendEmailResponse,
            )
            async def send_email(self, request):
                return {"queued": True, "job_id": "abc"}
        
        config = TestViewSet.send_email._action_config
        assert config["response_schema"] is SendEmailResponse
    
    def test_action_with_both_schemas(self):
        from pydantic import BaseModel
        from zeeb_api.viewsets import action, ModelViewSet
        
        class SendEmailRequest(BaseModel):
            subject: str
            body: str
        
        class SendEmailResponse(BaseModel):
            queued: bool
            job_id: str
        
        class TestViewSet(ModelViewSet):
            @action(
                detail=False,
                methods=["post"],
                request_schema=SendEmailRequest,
                response_schema=SendEmailResponse,
            )
            async def send_email(self, request):
                data = self.get_action_request_body()
                return {"queued": True, "job_id": "abc"}
        
        config = TestViewSet.send_email._action_config
        assert config["request_schema"] is SendEmailRequest
        assert config["response_schema"] is SendEmailResponse
    
    def test_action_with_serializer(self):
        from zeeb_api.viewsets import action, ModelViewSet
        from zeeb_api.serializers import Serializer, CharField
        
        class EmailSerializer(Serializer):
            subject = CharField()
            body = CharField()
        
        class TestViewSet(ModelViewSet):
            @action(
                detail=False,
                methods=["post"],
                request_serializer=EmailSerializer,
            )
            async def send_email(self, request):
                return {"queued": True}
        
        config = TestViewSet.send_email._action_config
        assert config["request_serializer"] is EmailSerializer
    
    def test_action_with_permission_classes(self):
        from zeeb_api.viewsets import action, ModelViewSet
        from zeeb_api.permissions import IsAuthenticated
        
        class TestViewSet(ModelViewSet):
            @action(
                detail=True,
                methods=["post"],
                permission_classes=[IsAuthenticated],
            )
            async def approve(self, request, pk=None):
                return {"status": "approved"}
        
        config = TestViewSet.approve._action_config
        assert config["permission_classes"] == [IsAuthenticated]
    
    def test_action_backward_compatible(self):
        """Existing actions without new params should still work."""
        from zeeb_api.viewsets import action, ModelViewSet
        
        class TestViewSet(ModelViewSet):
            @action(detail=True, methods=["post"])
            async def activate(self, request, pk=None):
                return {"status": "activated"}
        
        config = TestViewSet.activate._action_config
        assert config["detail"] is True
        assert config["methods"] == ["POST"]
        assert config["request_schema"] is None
        assert config["response_schema"] is None
        assert config["permission_classes"] is None


class TestViewSetActionPermissions:
    """Test action-specific permissions in ViewSet."""
    
    @pytest.mark.asyncio
    async def test_action_permissions_override_class_permissions(self):
        from zeeb_api.viewsets import ViewSet
        from zeeb_api.permissions import AllowAny, IsAuthenticated
        from unittest.mock import MagicMock
        
        class TestViewSet(ViewSet):
            permission_classes = [AllowAny]
        
        # Without action-specific permissions
        viewset = TestViewSet()
        permissions = viewset.get_permissions()
        assert len(permissions) == 1
        assert isinstance(permissions[0], AllowAny)
        
        # With action-specific permissions
        viewset2 = TestViewSet()
        viewset2._action_permission_classes = [IsAuthenticated]
        permissions2 = viewset2.get_permissions()
        assert len(permissions2) == 1
        assert isinstance(permissions2[0], IsAuthenticated)
    
    def test_get_action_request_body(self):
        from zeeb_api.viewsets import ViewSet
        
        viewset = ViewSet()
        assert viewset.get_action_request_body() is None
        
        viewset._request_body = {"subject": "Test", "body": "Hello"}
        assert viewset.get_action_request_body() == {"subject": "Test", "body": "Hello"}
