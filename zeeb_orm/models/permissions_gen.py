"""Permission rule collection and generated check/filter methods."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from zeeb_orm.permissions.rules import Rule

# Permission attribute suffixes
PERMISSION_ATTRS = ("read_permission", "add_permission", "change_permission", "delete_permission")


def _setup_permissions(model_class: type, namespace: dict[str, Any]) -> None:
    """
    Set up permission rules and generate check methods.

    Looks for *_permission attributes and generates corresponding
    check_*_permission() and get_*_filter() methods.
    """
    from zeeb_orm.permissions.rules import Rule

    # Collect permission rules from class and parents
    permission_rules: dict[str, Rule] = {}

    # Inherit from parents
    for parent in model_class.__mro__[1:]:
        for attr_name in PERMISSION_ATTRS:
            if hasattr(parent, attr_name):
                rule = getattr(parent, attr_name)
                if isinstance(rule, Rule) and attr_name not in permission_rules:
                    permission_rules[attr_name] = rule

    # Collect from this class (overrides parents)
    for attr_name in PERMISSION_ATTRS:
        if attr_name in namespace:
            rule = namespace[attr_name]
            if isinstance(rule, Rule):
                permission_rules[attr_name] = rule

    # Store rules on model
    model_class._permission_rules = permission_rules  # type: ignore[attr-defined]

    # Generate methods for each permission type
    for attr_name, rule in permission_rules.items():
        permission_type = attr_name.replace("_permission", "")  # "read", "add", etc.

        # Generate check method
        check_method_name = f"check_{permission_type}_permission"
        if check_method_name not in namespace:
            check_method = _make_check_method(permission_type, rule)
            setattr(model_class, check_method_name, check_method)

        # Generate filter method
        filter_method_name = f"get_{permission_type}_filter"
        if filter_method_name not in namespace:
            filter_method = _make_filter_method(permission_type, rule)
            setattr(model_class, filter_method_name, filter_method)


def _make_check_method(permission_type: str, rule: Rule):
    """Create an async check_*_permission method."""

    if permission_type == "add":
        # add_permission is a classmethod (no object yet)
        @classmethod
        async def check_add_permission(cls, user: Any) -> bool:
            """
            Check if user has add permission for this model.

            Args:
                user: User to check (can be None for anonymous)

            Returns:
                True if permission granted, False otherwise
            """
            return await rule.check(None, user)

        return check_add_permission

    else:
        # Other permissions are instance methods
        async def check_permission(self, user: Any) -> bool:
            """
            Check if user has permission for this object.

            Args:
                user: User to check (can be None for anonymous)

            Returns:
                True if permission granted, False otherwise
            """
            return await rule.check(self, user)

        check_permission.__doc__ = f"""
            Check if user has {permission_type} permission for this object.

            Args:
                user: User to check (can be None for anonymous)

            Returns:
                True if permission granted, False otherwise
            """
        check_permission.__name__ = f"check_{permission_type}_permission"

        return check_permission


def _make_filter_method(permission_type: str, rule: Rule):
    """Create a classmethod get_*_filter method."""

    @classmethod
    def get_filter(cls, user: Any):
        """
        Get Q filter for {permission_type} permission.

        Args:
            user: User to generate filter for (can be None for anonymous)

        Returns:
            Q filter object
        """
        return rule.to_q(user, cls)

    get_filter.__func__.__doc__ = f"""
        Get Q filter for {permission_type} permission.

        Args:
            user: User to generate filter for (can be None for anonymous)

        Returns:
            Q filter object
        """
    get_filter.__func__.__name__ = f"get_{permission_type}_filter"

    return get_filter


__all__ = [
    "PERMISSION_ATTRS",
    "_setup_permissions",
    "_make_check_method",
    "_make_filter_method",
]
