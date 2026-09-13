"""
Permission Rule class for composable, declarative permission definitions.

Rules can be combined with & (AND), | (OR), and ~ (NOT) operators.
They generate both object-level checks and Q filters for database queries.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Awaitable

if TYPE_CHECKING:
    from zeeb_orm.models.base import Model
    from zeeb_orm.query.q import Q as QFilter


class Rule:
    """
    Composable permission rule that generates both object checks and Q filters.
    
    Usage:
        # Simple rules
        Rule.staff()           # User is staff
        Rule.authenticated()   # User is logged in
        Rule.public()          # Anyone (including anonymous)
        Rule.owner("author")   # User matches author field
        Rule.Q(status="active") # Field condition
        
        # Combined rules
        Rule.Q(status="published") | Rule.owner("author")
        Rule.authenticated() & Rule.Q(role="editor")
        
        # Custom async check
        Rule.custom(my_async_check_fn)
    """
    
    # Rule types
    TYPE_OWNER = "owner"
    TYPE_STAFF = "staff"
    TYPE_SUPERUSER = "superuser"
    TYPE_AUTHENTICATED = "authenticated"
    TYPE_PUBLIC = "public"
    TYPE_Q = "q"
    TYPE_CUSTOM = "custom"
    TYPE_COMBINED = "combined"
    
    def __init__(
        self,
        rule_type: str,
        *,
        owner_field: str | None = None,
        q_kwargs: dict[str, Any] | None = None,
        custom_fn: Callable[[Any, Any], Awaitable[bool]] | None = None,
        children: list[Rule] | None = None,
        connector: str = "OR",  # "OR" or "AND"
        negated: bool = False,
    ):
        self.rule_type = rule_type
        self.owner_field = owner_field
        self.q_kwargs = q_kwargs or {}
        self.custom_fn = custom_fn
        self.children = children or []
        self.connector = connector
        self.negated = negated
    
    # =========================================================================
    # Factory methods for creating rules
    # =========================================================================
    
    @classmethod
    def owner(cls, field: str) -> Rule:
        """
        User matches the specified field (ForeignKey or ID field).
        
        Args:
            field: Name of the field containing owner reference.
                   For ForeignKey "author", checks both author_id and author.id
        
        Example:
            Rule.owner("author")  # user.id == obj.author_id
        """
        return cls(cls.TYPE_OWNER, owner_field=field)
    
    @classmethod
    def staff(cls) -> Rule:
        """
        User has staff privileges (is_staff=True).
        
        Example:
            Rule.staff()  # user.is_staff == True
        """
        return cls(cls.TYPE_STAFF)
    
    @classmethod
    def superuser(cls) -> Rule:
        """
        User is a superuser (is_superuser=True).
        
        Example:
            Rule.superuser()  # user.is_superuser == True
        """
        return cls(cls.TYPE_SUPERUSER)
    
    @classmethod
    def authenticated(cls) -> Rule:
        """
        User is authenticated (not None).
        
        Example:
            Rule.authenticated()  # user is not None
        """
        return cls(cls.TYPE_AUTHENTICATED)
    
    @classmethod
    def public(cls) -> Rule:
        """
        Anyone can access, including anonymous users.
        
        Example:
            Rule.public()  # Always True
        """
        return cls(cls.TYPE_PUBLIC)
    
    @classmethod
    def Q(cls, **kwargs) -> Rule:
        """
        Field condition using Q-filter syntax.
        
        Args:
            **kwargs: Field lookups (same syntax as QuerySet.filter)
        
        Example:
            Rule.Q(status="published")
            Rule.Q(is_active=True, role="editor")
            Rule.Q(created_at__gte=some_date)
        """
        return cls(cls.TYPE_Q, q_kwargs=kwargs)
    
    @classmethod
    def custom(cls, check_fn: Callable[[Any, Any], Awaitable[bool]]) -> Rule:
        """
        Custom async check function.
        
        The function receives (obj, user) and should return bool.
        For class-level checks (add permission), obj is None.
        
        Note: Custom rules cannot generate Q filters for database queries.
        They will be evaluated in Python after the query.
        
        Args:
            check_fn: Async function (obj, user) -> bool
        
        Example:
            async def check_membership(obj, user):
                if user is None:
                    return False
                return await user.groups.filter(name="editors").exists()
            
            Rule.custom(check_membership)
        """
        return cls(cls.TYPE_CUSTOM, custom_fn=check_fn)
    
    # =========================================================================
    # Operators for combining rules
    # =========================================================================
    
    def __or__(self, other: Rule) -> Rule:
        """
        Combine rules with OR.
        
        Example:
            Rule.Q(status="published") | Rule.owner("author")
        """
        if not isinstance(other, Rule):
            return NotImplemented
        return Rule(
            self.TYPE_COMBINED,
            children=[self, other],
            connector="OR",
        )
    
    def __and__(self, other: Rule) -> Rule:
        """
        Combine rules with AND.
        
        Example:
            Rule.authenticated() & Rule.Q(role="editor")
        """
        if not isinstance(other, Rule):
            return NotImplemented
        return Rule(
            self.TYPE_COMBINED,
            children=[self, other],
            connector="AND",
        )
    
    def __invert__(self) -> Rule:
        """
        Negate rule with NOT.
        
        Example:
            ~Rule.Q(status="draft")  # status != "draft"
        """
        return Rule(
            self.rule_type,
            owner_field=self.owner_field,
            q_kwargs=self.q_kwargs,
            custom_fn=self.custom_fn,
            children=self.children,
            connector=self.connector,
            negated=not self.negated,
        )
    
    # =========================================================================
    # Check methods
    # =========================================================================
    
    async def check(self, obj: Model | None, user: Any) -> bool:
        """
        Check if permission is granted for a specific object.
        
        Args:
            obj: Model instance to check (None for add permission)
            user: User to check permission for (can be None for anonymous)
        
        Returns:
            True if permission is granted, False otherwise
        """
        result = await self._check_internal(obj, user)
        return not result if self.negated else result
    
    async def _check_internal(self, obj: Model | None, user: Any) -> bool:
        """Internal check without negation handling."""
        
        if self.rule_type == self.TYPE_PUBLIC:
            return True
        
        if self.rule_type == self.TYPE_AUTHENTICATED:
            return user is not None
        
        if self.rule_type == self.TYPE_STAFF:
            if user is None:
                return False
            return (
                getattr(user, "is_staff", False) or 
                getattr(user, "is_superuser", False)
            )
        
        if self.rule_type == self.TYPE_SUPERUSER:
            if user is None:
                return False
            return getattr(user, "is_superuser", False)
        
        if self.rule_type == self.TYPE_OWNER:
            if user is None or obj is None:
                return False
            return self._check_owner(obj, user)
        
        if self.rule_type == self.TYPE_Q:
            if obj is None:
                # Q conditions don't apply to class-level checks
                return True
            return self._check_q_condition(obj)
        
        if self.rule_type == self.TYPE_CUSTOM:
            if self.custom_fn is None:
                return False
            return await self.custom_fn(obj, user)
        
        if self.rule_type == self.TYPE_COMBINED:
            return await self._check_combined(obj, user)
        
        return False
    
    def _check_owner(self, obj: Model, user: Any) -> bool:
        """Check if user owns the object."""
        if self.owner_field is None:
            return False
        
        # Get user ID
        user_id = getattr(user, "id", None) or getattr(user, "pk", None)
        if user_id is None:
            return False
        
        # Try field_id first (FK convention)
        owner_id = getattr(obj, f"{self.owner_field}_id", None)
        if owner_id is None:
            # Try direct field access
            owner = getattr(obj, self.owner_field, None)
            if owner is not None:
                owner_id = getattr(owner, "id", None) or getattr(owner, "pk", owner)
        
        if owner_id is None:
            return False
        
        # Compare as strings to handle UUID vs string comparisons
        return str(owner_id) == str(user_id)
    
    def _check_q_condition(self, obj: Model) -> bool:
        """Check if object matches Q conditions."""
        for field_lookup, expected_value in self.q_kwargs.items():
            # Parse field lookup (e.g., "status" or "created_at__gte")
            parts = field_lookup.split("__")
            field_name = parts[0]
            lookup = parts[1] if len(parts) > 1 else "exact"
            
            # Get actual value from object
            actual_value = getattr(obj, field_name, None)
            
            # Evaluate based on lookup type
            if not self._evaluate_lookup(actual_value, lookup, expected_value):
                return False
        
        return True
    
    def _evaluate_lookup(self, actual: Any, lookup: str, expected: Any) -> bool:
        """Evaluate a single lookup condition."""
        if lookup == "exact":
            return actual == expected
        elif lookup == "iexact":
            return str(actual).lower() == str(expected).lower()
        elif lookup == "contains":
            return expected in str(actual)
        elif lookup == "icontains":
            return str(expected).lower() in str(actual).lower()
        elif lookup == "gt":
            return actual > expected
        elif lookup == "gte":
            return actual >= expected
        elif lookup == "lt":
            return actual < expected
        elif lookup == "lte":
            return actual <= expected
        elif lookup == "in":
            return actual in expected
        elif lookup == "isnull":
            return (actual is None) == expected
        elif lookup == "startswith":
            return str(actual).startswith(expected)
        elif lookup == "istartswith":
            return str(actual).lower().startswith(str(expected).lower())
        elif lookup == "endswith":
            return str(actual).endswith(expected)
        elif lookup == "iendswith":
            return str(actual).lower().endswith(str(expected).lower())
        else:
            # Unknown lookup, default to exact
            return actual == expected
    
    async def _check_combined(self, obj: Model | None, user: Any) -> bool:
        """Check combined rules with AND/OR logic."""
        if not self.children:
            return True
        
        if self.connector == "AND":
            for child in self.children:
                if not await child.check(obj, user):
                    return False
            return True
        else:  # OR
            for child in self.children:
                if await child.check(obj, user):
                    return True
            return False
    
    # =========================================================================
    # Q filter generation for database queries
    # =========================================================================
    
    def to_q(self, user: Any, model_class: type[Model] | None = None) -> QFilter:
        """
        Generate Q filter for queryset filtering.
        
        Some rules (staff, superuser, authenticated) cannot be expressed as
        Q filters since they depend on user attributes, not object attributes.
        These rules return either Q() (match all) or Q(pk=None) (match none).
        
        Args:
            user: User to generate filter for
            model_class: Model class (for resolving field references)
        
        Returns:
            Q filter object
        """
        from zeeb_orm.query.q import Q
        
        q_filter = self._to_q_internal(user, model_class)
        
        if self.negated:
            return ~q_filter
        return q_filter
    
    def _to_q_internal(self, user: Any, model_class: type[Model] | None) -> QFilter:
        """Internal Q generation without negation handling."""
        from zeeb_orm.query.q import Q
        
        if self.rule_type == self.TYPE_PUBLIC:
            # Match everything
            return Q()
        
        if self.rule_type == self.TYPE_AUTHENTICATED:
            if user is None:
                # Match nothing
                return Q(pk=None)
            # Match everything (user is authenticated)
            return Q()
        
        if self.rule_type == self.TYPE_STAFF:
            if user is None:
                return Q(pk=None)
            is_staff = (
                getattr(user, "is_staff", False) or 
                getattr(user, "is_superuser", False)
            )
            if is_staff:
                return Q()  # Match everything
            return Q(pk=None)  # Match nothing
        
        if self.rule_type == self.TYPE_SUPERUSER:
            if user is None:
                return Q(pk=None)
            if getattr(user, "is_superuser", False):
                return Q()
            return Q(pk=None)
        
        if self.rule_type == self.TYPE_OWNER:
            if user is None:
                return Q(pk=None)
            user_id = getattr(user, "id", None) or getattr(user, "pk", None)
            if user_id is None:
                return Q(pk=None)
            # Filter by owner field
            return Q(**{f"{self.owner_field}_id": user_id})
        
        if self.rule_type == self.TYPE_Q:
            return Q(**self.q_kwargs)
        
        if self.rule_type == self.TYPE_CUSTOM:
            # Custom rules can't be converted to Q filters
            # Return Q() and let Python-level filtering handle it
            return Q()
        
        if self.rule_type == self.TYPE_COMBINED:
            return self._to_q_combined(user, model_class)
        
        return Q()
    
    def _to_q_combined(self, user: Any, model_class: type[Model] | None) -> QFilter:
        """Generate Q filter for combined rules."""
        from zeeb_orm.query.q import Q
        
        if not self.children:
            return Q()
        
        result = self.children[0].to_q(user, model_class)
        
        for child in self.children[1:]:
            child_q = child.to_q(user, model_class)
            if self.connector == "AND":
                result = result & child_q
            else:  # OR
                result = result | child_q
        
        return result
    
    # =========================================================================
    # Utility methods
    # =========================================================================
    
    def has_custom_rules(self) -> bool:
        """Check if this rule or any children contain custom rules."""
        if self.rule_type == self.TYPE_CUSTOM:
            return True
        for child in self.children:
            if child.has_custom_rules():
                return True
        return False
    
    def __repr__(self) -> str:
        neg = "~" if self.negated else ""
        if self.rule_type == self.TYPE_COMBINED:
            connector = f" {self.connector} "
            children_repr = connector.join(repr(c) for c in self.children)
            return f"{neg}({children_repr})"
        elif self.rule_type == self.TYPE_OWNER:
            return f"{neg}Rule.owner({self.owner_field!r})"
        elif self.rule_type == self.TYPE_Q:
            return f"{neg}Rule.Q({self.q_kwargs})"
        elif self.rule_type == self.TYPE_CUSTOM:
            return f"{neg}Rule.custom(...)"
        else:
            return f"{neg}Rule.{self.rule_type}()"
