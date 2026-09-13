"""Manager class providing the interface for database queries."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Generic, TypeVar

if TYPE_CHECKING:
    from zeeb_orm.models.base import Model
    from zeeb_orm.query.queryset import QuerySet

ModelT = TypeVar("ModelT", bound="Model")


class Manager(Generic[ModelT]):
    """
    Django-style Manager class that provides the interface to database queries.

    Each model gets a default Manager accessible via Model.objects.

    Usage:
        User.objects.all()
        User.objects.filter(active=True)
        User.objects.get(id=1)
        User.objects.create(name='John')
    """

    #: Set by :meth:`from_queryset` on dynamically built Manager subclasses.
    _built_with_queryset: type | None = None

    def __init__(self) -> None:
        self.model: type[ModelT] | None = None
        self._name: str = "objects"
        self._original_model: type[ModelT] | None = None

    @classmethod
    def from_queryset(
        cls, queryset_class: type, class_name: str | None = None
    ) -> type[Manager[Any]]:
        """Build a Manager subclass exposing ``queryset_class``'s methods.

        Every public (non-underscore) method of ``queryset_class`` that is
        not already defined on the Manager class is copied as a proxy that
        calls the same method on ``self.get_queryset()``.  The generated
        manager's ``get_queryset()`` returns ``queryset_class(self.model)``.

        The returned class is zero-arg constructible (required for the
        descriptor rebinding in :meth:`__get__`).

        Usage:
            class PostQuerySet(QuerySet):
                def published(self):
                    return self.filter(published=True)

            class Post(Model):
                objects = Manager.from_queryset(PostQuerySet)()
        """
        if class_name is None:
            class_name = f"{cls.__name__}From{queryset_class.__name__}"

        def get_queryset(self: Manager[Any]) -> Any:
            if self.model is None:
                raise RuntimeError("Manager is not bound to a model")
            return queryset_class(self.model)

        get_queryset.__doc__ = (
            f"Return a new {queryset_class.__name__} bound to this "
            "manager's model."
        )

        attrs: dict[str, Any] = {
            "_built_with_queryset": queryset_class,
            "get_queryset": get_queryset,
        }
        attrs.update(cls._get_queryset_methods(queryset_class))
        return type(class_name, (cls,), attrs)

    @classmethod
    def _get_queryset_methods(cls, queryset_class: type) -> dict[str, Any]:
        """Proxy methods for public queryset methods not already on ``cls``."""
        import inspect

        def create_method(name: str, method: Any) -> Any:
            def manager_method(self: Manager[Any], *args: Any, **kwargs: Any) -> Any:
                return getattr(self.get_queryset(), name)(*args, **kwargs)

            manager_method.__name__ = name
            manager_method.__qualname__ = name
            manager_method.__doc__ = method.__doc__
            return manager_method

        new_methods: dict[str, Any] = {}
        for name, method in inspect.getmembers(
            queryset_class, predicate=inspect.isfunction
        ):
            if name.startswith("_"):
                continue
            if hasattr(cls, name):
                continue
            new_methods[name] = create_method(name, method)
        return new_methods

    def __set_name__(self, owner: type, name: str) -> None:
        self._name = name

    def __get__(
        self, obj: ModelT | None, objtype: type[ModelT] | None = None
    ) -> Manager[ModelT]:
        """Descriptor protocol - returns manager bound to model class."""
        if objtype is None:
            raise AttributeError("Manager is not accessible via model instances")
        
        # Check if this is being accessed from the original model class
        # or from a subclass of Model
        from zeeb_orm.models.base import Model
        if not issubclass(objtype, Model):
            # Being accessed from a non-Model class (e.g., ViewSet)
            # Return a manager bound to the original model
            if self._original_model is not None:
                manager = self.__class__()
                manager.model = self._original_model
                manager._name = self._name
                manager._original_model = self._original_model
                return manager
            # Fall back to self.model if _original_model not set
            if self.model is not None:
                manager = self.__class__()
                manager.model = self.model
                manager._name = self._name
                manager._original_model = self.model
                return manager
        
        # Return a copy bound to the model class
        manager = self.__class__()
        manager.model = objtype
        manager._name = self._name
        manager._original_model = objtype
        return manager

    def contribute_to_class(self, model: type[ModelT], name: str) -> None:
        """Called when manager is added to a model class."""
        self.model = model
        self._name = name
        self._original_model = model
        setattr(model, name, self)

    def _get_queryset(self) -> QuerySet[ModelT]:
        """Create a new QuerySet for this manager's model."""
        from zeeb_orm.query.queryset import QuerySet

        if self.model is None:
            raise RuntimeError("Manager is not bound to a model")
        return QuerySet(self.model)

    def get_queryset(self) -> QuerySet[ModelT]:
        """
        Return a new QuerySet. Override in subclasses to customize default queryset.
        """
        return self._get_queryset()

    # QuerySet proxy methods

    def all(self) -> QuerySet[ModelT]:
        """Return all objects."""
        return self.get_queryset()

    def none(self) -> QuerySet[ModelT]:
        """Return a queryset guaranteed to match nothing."""
        return self.get_queryset().none()

    def filter(self, *args: Any, **kwargs: Any) -> QuerySet[ModelT]:
        """Return filtered queryset."""
        return self.get_queryset().filter(*args, **kwargs)

    def exclude(self, *args: Any, **kwargs: Any) -> QuerySet[ModelT]:
        """Return queryset excluding matching objects."""
        return self.get_queryset().exclude(*args, **kwargs)

    async def get(self, *args: Any, **kwargs: Any) -> ModelT:
        """Get a single object matching the query."""
        return await self.get_queryset().get(*args, **kwargs)

    async def first(self) -> ModelT | None:
        """Get the first object."""
        return await self.get_queryset().first()

    async def last(self) -> ModelT | None:
        """Get the last object."""
        return await self.get_queryset().last()

    async def count(self) -> int:
        """Count all objects."""
        return await self.get_queryset().count()

    async def exists(self) -> bool:
        """Check if any objects exist."""
        return await self.get_queryset().exists()

    async def create(self, *, validate: bool = True, **kwargs: Any) -> ModelT:
        """Create and save a new object (validated unless ``validate=False``)."""
        return await self.get_queryset().create(validate=validate, **kwargs)

    async def in_bulk(
        self, id_list: list[Any] | None = None, *, field_name: str = "pk"
    ) -> dict[Any, ModelT]:
        """Return a ``{field_value: instance}`` mapping for the given values."""
        return await self.get_queryset().in_bulk(id_list, field_name=field_name)

    def iterator(self, chunk_size: int = 2000) -> Any:
        """Stream results in chunks without result caching."""
        return self.get_queryset().iterator(chunk_size=chunk_size)

    async def get_or_create(
        self, defaults: dict[str, Any] | None = None, **kwargs: Any
    ) -> tuple[ModelT, bool]:
        """Get an object or create it if it doesn't exist."""
        return await self.get_queryset().get_or_create(defaults=defaults, **kwargs)

    async def update_or_create(
        self, defaults: dict[str, Any] | None = None, **kwargs: Any
    ) -> tuple[ModelT, bool]:
        """Update an object or create it if it doesn't exist."""
        return await self.get_queryset().update_or_create(defaults=defaults, **kwargs)

    async def bulk_create(
        self,
        objs: list[ModelT],
        *,
        batch_size: int | None = None,
        ignore_conflicts: bool = False,
        validate: bool = False,
    ) -> list[ModelT]:
        """Insert multiple objects efficiently."""
        return await self.get_queryset().bulk_create(
            objs,
            batch_size=batch_size,
            ignore_conflicts=ignore_conflicts,
            validate=validate,
        )

    async def bulk_update(
        self,
        objs: list[ModelT],
        fields: list[str],
        *,
        batch_size: int | None = None,
    ) -> int:
        """Update multiple objects efficiently."""
        return await self.get_queryset().bulk_update(objs, fields, batch_size=batch_size)

    def order_by(self, *fields: str) -> QuerySet[ModelT]:
        """Return ordered queryset."""
        return self.get_queryset().order_by(*fields)

    def distinct(self, *fields: str) -> QuerySet[ModelT]:
        """Return queryset with distinct results."""
        return self.get_queryset().distinct(*fields)

    def values(self, *fields: str) -> QuerySet[ModelT]:
        """Return queryset yielding dictionaries."""
        return self.get_queryset().values(*fields)

    def values_list(self, *fields: str, flat: bool = False) -> QuerySet[ModelT]:
        """Return queryset yielding tuples."""
        return self.get_queryset().values_list(*fields, flat=flat)

    def only(self, *fields: str) -> QuerySet[ModelT]:
        """Load only specified fields."""
        return self.get_queryset().only(*fields)

    def defer(self, *fields: str) -> QuerySet[ModelT]:
        """Defer loading of specified fields."""
        return self.get_queryset().defer(*fields)

    def select_related(self, *fields: str) -> QuerySet[ModelT]:
        """Load related objects in the same query (JOIN)."""
        return self.get_queryset().select_related(*fields)

    def prefetch_related(self, *lookups: Any) -> QuerySet[ModelT]:
        """Prefetch related objects in separate queries."""
        return self.get_queryset().prefetch_related(*lookups)

    def annotate(self, **kwargs: Any) -> QuerySet[ModelT]:
        """Add annotations (computed fields) to results."""
        return self.get_queryset().annotate(**kwargs)

    def aggregate(self, **kwargs: Any) -> QuerySet[ModelT]:
        """Compute aggregate values."""
        return self.get_queryset().aggregate(**kwargs)

    def using(self, alias: str) -> QuerySet[ModelT]:
        """Use a specific database."""
        return self.get_queryset().using(alias)

    def raw(self, sql: str, params: list[Any] | None = None) -> QuerySet[ModelT]:
        """Execute raw SQL query."""
        return self.get_queryset().raw(sql, params)

    def union(self, *other_qs: Any, all: bool = False) -> QuerySet[ModelT]:
        """Combine with other querysets using SQL UNION."""
        return self.get_queryset().union(*other_qs, all=all)

    def intersection(self, *other_qs: Any) -> QuerySet[ModelT]:
        """Combine with other querysets using SQL INTERSECT."""
        return self.get_queryset().intersection(*other_qs)

    def difference(self, *other_qs: Any) -> QuerySet[ModelT]:
        """Combine with other querysets using SQL EXCEPT."""
        return self.get_queryset().difference(*other_qs)

    def select_for_update(
        self,
        *,
        nowait: bool = False,
        skip_locked: bool = False,
        of: tuple[str, ...] | list[str] = (),
    ) -> QuerySet[ModelT]:
        """Lock the selected rows with ``SELECT ... FOR UPDATE``."""
        return self.get_queryset().select_for_update(
            nowait=nowait, skip_locked=skip_locked, of=of
        )

    async def explain(self, *, analyze: bool = False) -> str:
        """Return the database's execution plan for the full-table query."""
        return await self.get_queryset().explain(analyze=analyze)


class RelatedManager(Manager[ModelT]):
    """
    Manager for reverse ForeignKey relations.
    
    Provides Django-style access to related objects:
        user.posts.all()
        user.posts.filter(published=True)
        user.posts.create(title='New Post')
    """

    def __init__(
        self,
        model: type[ModelT],
        instance: Any,
        field_name: str,
        fk_field_name: str,
    ) -> None:
        super().__init__()
        self.model = model
        self._instance = instance
        self._field_name = field_name
        self._fk_field_name = fk_field_name

    def __get__(
        self, obj: Any | None, objtype: type[Any] | None = None
    ) -> RelatedManager[ModelT]:
        """Return self - we're already bound to an instance."""
        return self

    def get_queryset(self) -> QuerySet[ModelT]:
        """Return queryset filtered to related objects."""
        from zeeb_orm.query.queryset import QuerySet

        if self.model is None:
            raise RuntimeError("RelatedManager is not bound to a model")

        # Filter by the FK pointing to our instance
        pk_value = self._instance.pk
        return QuerySet(self.model).filter(**{self._fk_field_name: pk_value})

    async def add(self, *objs: ModelT) -> None:
        """Add objects to the relation by setting their FK."""
        pk_value = self._instance.pk
        for obj in objs:
            setattr(obj, self._fk_field_name, pk_value)
            await obj.save()

    async def remove(self, *objs: ModelT) -> None:
        """Remove objects from the relation by clearing their FK."""
        for obj in objs:
            setattr(obj, self._fk_field_name, None)
            await obj.save()

    async def clear(self) -> None:
        """Remove all objects from the relation."""
        await self.get_queryset().update(**{self._fk_field_name: None})

    async def set(self, objs: list[ModelT]) -> None:
        """Replace the set of related objects."""
        await self.clear()
        for obj in objs:
            await self.add(obj)

    async def create(self, **kwargs: Any) -> ModelT:
        """Create a new related object with FK already set."""
        kwargs[self._fk_field_name] = self._instance.pk
        return await super().create(**kwargs)

    def __repr__(self) -> str:
        return f"<RelatedManager: {self.model.__name__ if self.model else 'unbound'}>"


class RelatedManagerDescriptor:
    """
    Descriptor that creates a RelatedManager for each instance.
    
    This is set on the "one" side of a ForeignKey relation.
    """

    def __init__(
        self,
        related_model: type[ModelT],
        field_name: str,
        fk_field_name: str,
    ) -> None:
        self.related_model = related_model
        self.field_name = field_name
        self.fk_field_name = fk_field_name

    def __get__(self, obj: Any | None, objtype: type | None = None) -> Any:
        if obj is None:
            return self
        
        # Return a RelatedManager bound to this instance
        return RelatedManager(
            model=self.related_model,
            instance=obj,
            field_name=self.field_name,
            fk_field_name=self.fk_field_name,
        )
