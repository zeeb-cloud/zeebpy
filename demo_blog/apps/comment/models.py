"""
comment models.

Define your Zeeb ORM models here.
"""

from zeeb_orm import Model, fields


# Example model:
class Comment(Model):
    name = fields.CharField(max_length=100)
    description = fields.TextField(null=True)
    created_at = fields.DateTimeField(auto_now_add=True)
    updated_at = fields.DateTimeField(auto_now=True)

    class Meta:
        table_name = "comment_comment"
        ordering = ["-created_at"]
