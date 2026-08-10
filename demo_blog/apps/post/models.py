"""post models."""

from zeeb_orm import Model, fields


class Post(Model):
    """A blog post, owned by the user who wrote it."""

    title = fields.CharField(max_length=200)
    slug = fields.SlugField(max_length=220, unique=True)
    body = fields.TextField()
    published = fields.BooleanField(default=False)
    author = fields.ForeignKey("accounts.User", on_delete="CASCADE", related_name="posts")
    created_at = fields.DateTimeField(auto_now_add=True)
    updated_at = fields.DateTimeField(auto_now=True)

    class Meta:
        table_name = "post_post"
        ordering = ["-created_at"]
