"""Tests for zeeb_orm.models.relations.resolve_relation (no database needed)."""

from zeeb_orm import Model, fields
from zeeb_orm.models.relations import RelationInfo, resolve_relation

# Test Models


class RelAuthor(Model):
    """Author model for relation-resolution tests."""

    name = fields.CharField(max_length=100)

    class Meta:
        table_name = "rel_authors"


class RelProfile(Model):
    """One-to-one profile for RelAuthor."""

    author = fields.OneToOne(RelAuthor, related_name="profile")
    bio = fields.TextField(null=True)

    class Meta:
        table_name = "rel_profiles"


class RelPost(Model):
    """Post with FK to RelAuthor (explicit related_name)."""

    title = fields.CharField(max_length=200)
    author = fields.ForeignKey(RelAuthor, related_name="rel_posts")

    class Meta:
        table_name = "rel_posts"


class RelComment(Model):
    """Comment with FK to RelPost (no related_name -> default `_set`)."""

    text = fields.TextField()
    post = fields.ForeignKey(RelPost)

    class Meta:
        table_name = "rel_comments"


class TestForwardRelations:
    """Forward FK / O2O resolution."""

    def test_forward_fk(self):
        info = resolve_relation(RelPost, "author")
        assert isinstance(info, RelationInfo)
        assert info.kind == "fk"
        assert info.source_model is RelPost
        assert info.target_model is RelAuthor
        assert info.accessor_name == "author"
        assert info.fk_field is not None
        assert info.fk_field.name == "author"

    def test_forward_fk_column(self):
        info = resolve_relation(RelPost, "author")
        assert info is not None
        assert info.fk_column == "author_id"

    def test_forward_o2o(self):
        info = resolve_relation(RelProfile, "author")
        assert info is not None
        assert info.kind == "o2o"
        assert info.source_model is RelProfile
        assert info.target_model is RelAuthor
        assert info.fk_column == "author_id"

    def test_forward_fk_second_model(self):
        info = resolve_relation(RelComment, "post")
        assert info is not None
        assert info.kind == "fk"
        assert info.target_model is RelPost
        assert info.fk_column == "post_id"


class TestReverseRelations:
    """Reverse FK / O2O resolution via related_name or default `_set`."""

    def test_reverse_fk_related_name(self):
        info = resolve_relation(RelAuthor, "rel_posts")
        assert info is not None
        assert info.kind == "reverse_fk"
        assert info.source_model is RelAuthor
        assert info.target_model is RelPost
        assert info.accessor_name == "rel_posts"
        # The implementing FK column lives on the target (RelPost) table
        assert info.fk_column == "author_id"
        assert info.fk_field is not None
        assert info.fk_field.name == "author"

    def test_reverse_fk_default_set_name(self):
        info = resolve_relation(RelPost, "relcomment_set")
        assert info is not None
        assert info.kind == "reverse_fk"
        assert info.source_model is RelPost
        assert info.target_model is RelComment
        assert info.fk_column == "post_id"

    def test_reverse_o2o(self):
        info = resolve_relation(RelAuthor, "profile")
        assert info is not None
        assert info.kind == "reverse_o2o"
        assert info.source_model is RelAuthor
        assert info.target_model is RelProfile
        assert info.fk_column == "author_id"


class TestNonRelations:
    """Names that are not relations must return None."""

    def test_plain_field_is_not_a_relation(self):
        assert resolve_relation(RelPost, "title") is None
        assert resolve_relation(RelAuthor, "name") is None

    def test_unknown_name(self):
        assert resolve_relation(RelPost, "nonexistent") is None

    def test_default_set_name_not_used_when_related_name_given(self):
        # RelPost.author has related_name="rel_posts", so the default
        # `relpost_set` accessor must NOT resolve.
        assert resolve_relation(RelAuthor, "relpost_set") is None
        # Same for the O2O with related_name="profile"
        assert resolve_relation(RelAuthor, "relprofile_set") is None
