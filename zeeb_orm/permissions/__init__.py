"""
Per-object permission rules for zeeb_orm models.

This module provides a declarative way to define permission rules on models
that can be used for both object-level checks and database-level filtering.

Example:
    from zeeb_orm import Model, fields
    from zeeb_orm.permissions import Rule

    class Post(Model):
        title = fields.CharField(max_length=200)
        author = fields.ForeignKey("User", on_delete="CASCADE")
        status = fields.CharField(max_length=20, default="draft")
        
        # Permission rules as class attributes
        read_permission = Rule.Q(status="published") | Rule.owner("author") | Rule.staff()
        add_permission = Rule.authenticated()
        change_permission = Rule.owner("author") | Rule.staff()
        delete_permission = Rule.owner("author") | Rule.staff()
    
    # Object-level check
    if await post.check_change_permission(user):
        await post.save()
    
    # Database-level filtering
    posts = await Post.objects.readable_by(user).all()
"""

from zeeb_orm.permissions.rules import Rule

__all__ = [
    "Rule",
]
