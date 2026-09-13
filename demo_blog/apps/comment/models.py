"""comment models."""

from zeeb_orm import Model, fields


class Comment(Model):
    """A comment on a post."""

    post = fields.ForeignKey("post.Post", on_delete="CASCADE", related_name="comments")
    author = fields.ForeignKey("accounts.User", on_delete="CASCADE", related_name="comments")
    body = fields.TextField()
    created_at = fields.DateTimeField(auto_now_add=True)

    class Meta:
        table_name = "comment_comment"
        ordering = ["-created_at"]
