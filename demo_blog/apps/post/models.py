"""
post models.

Define your Zeeb ORM models here.
"""

from zeeb_orm import Model, fields


# Example model:
class Post(Model):
    name = fields.CharField(max_length=100)
    description = fields.TextField(null=True)
    created_at = fields.DateTimeField(auto_now_add=True)
    updated_at = fields.DateTimeField(auto_now=True)

    class Meta:
        table_name = "post_post"
        ordering = ["-created_at"]
