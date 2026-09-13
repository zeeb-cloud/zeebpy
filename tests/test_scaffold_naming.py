"""The naming helpers that decide model, table and route names.

A wrong guess here does not stay cosmetic: the singular form becomes a class
name and half of a table name, and the plural becomes the URL a client calls.
"""

import pytest

from zeeb_agents._utils import code_gen
from zeeb_orm.scaffold import naming
from zeeb_orm.scaffold.naming import pluralize, singularize, to_class_name


def test_both_layers_share_one_plural_rule():
    """A resource must land under the same prefix whichever layer created it."""
    assert code_gen.pluralize is naming.pluralize


@pytest.mark.parametrize(
    "word",
    [
        "post",
        "company",
        "box",
        "status",
        "address",
        "class",
        "category",
        "match",
        "note",
        "dish",
        "user",
        "comment",
        "tag",
        "analysis",
        "series",
    ],
)
def test_singularize_reverses_pluralize(word):
    assert singularize(pluralize(word)) == word


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        # The regressions a plain rstrip("s") produced.
        ("address", "address"),
        ("class", "class"),
        ("status", "status"),
        ("analysis", "analysis"),
        # Real plurals still lose their ending.
        ("posts", "post"),
        ("companies", "company"),
        ("boxes", "box"),
        ("notes", "note"),
        # Already singular, left alone.
        ("post", "post"),
        ("comment", "comment"),
    ],
)
def test_singularize_only_strips_what_it_recognizes(word, expected):
    assert singularize(word) == expected


@pytest.mark.parametrize(
    ("app_name", "expected_model"),
    [
        ("posts", "Post"),
        ("blog", "Blog"),
        ("address", "Address"),
        ("companies", "Company"),
        ("blog_posts", "BlogPost"),
        ("status", "Status"),
    ],
)
def test_the_app_name_yields_a_usable_model_name(app_name, expected_model):
    """This is the expression startapp uses to name its example model."""
    assert to_class_name(singularize(app_name)) == expected_model
