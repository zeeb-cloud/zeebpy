"""Q objects: deconstruction and the match_all / match_none constants."""

import pytest

from zeeb_orm import Q


class TestDeconstruct:
    def test_leaf_children_stay_tuples(self):
        assert Q(a=1, b=2).deconstruct() == {
            "connector": "AND",
            "negated": False,
            "children": [("a", 1), ("b", 2)],
        }

    def test_nested_q_children_are_kept_in_position(self):
        # Used to drop every nested Q: the comprehension only iterated tuples.
        leaf_a = {"connector": "AND", "negated": False, "children": [("a", 1)]}
        leaf_b = {"connector": "AND", "negated": False, "children": [("b", 2)]}
        q = Q(Q(a=1) | Q(b=2), c=3)
        assert q.deconstruct() == {
            "connector": "AND",
            "negated": False,
            "children": [
                {"connector": "OR", "negated": False, "children": [leaf_a, leaf_b]},
                ("c", 3),
            ],
        }

    def test_negated_nested_tree_keeps_every_level(self):
        data = (~(Q(a=1) & ~Q(b=2))).deconstruct()
        assert data["negated"] is True
        assert data["children"] == [
            {"connector": "AND", "negated": False, "children": [("a", 1)]},
            {"connector": "AND", "negated": True, "children": [("b", 2)]},
        ]

    def test_constants_are_marked(self):
        assert Q.match_all().deconstruct()["match"] == "all"
        assert Q.match_none().deconstruct()["match"] == "none"
        assert "match" not in Q(a=1).deconstruct()


class TestConstants:
    """match_all / match_none follow boolean algebra; empty Q() stays a no-op."""

    def test_or_with_match_all_is_match_all(self):
        assert (Q.match_all() | Q(a=1)).is_match_all
        assert (Q(a=1) | Q.match_all()).is_match_all

    def test_and_with_match_all_is_the_other_operand(self):
        q = Q(a=1)
        assert (Q.match_all() & q) is q
        assert (q & Q.match_all()) is q

    def test_and_with_match_none_is_match_none(self):
        assert (Q.match_none() & Q(a=1)).is_match_none
        assert (Q(a=1) & Q.match_none()).is_match_none

    def test_or_with_match_none_is_the_other_operand(self):
        q = Q(a=1)
        assert (Q.match_none() | q) is q
        assert (q | Q.match_none()) is q

    def test_negation_swaps_the_constants(self):
        assert (~Q.match_all()).is_match_none
        assert (~Q.match_none()).is_match_all

    def test_constants_combine_with_each_other(self):
        assert (Q.match_all() | Q.match_none()).is_match_all
        assert (Q.match_all() & Q.match_none()).is_match_none
        assert (Q.match_none() | Q.match_all()).is_match_all
        assert (Q.match_none() & Q.match_all()).is_match_none

    def test_empty_q_is_the_identity_even_for_constants(self):
        assert (Q() | Q.match_none()).is_match_none
        assert (Q.match_none() & Q()).is_match_none
        assert (Q() & Q.match_all()).is_match_all

    def test_empty_q_keeps_django_semantics(self):
        q = Q(a=1)
        assert (Q() | q) is q
        assert not ~Q()

    def test_constants_are_truthy(self):
        assert Q.match_all()
        assert Q.match_none()


@pytest.mark.parametrize(
    "q, expected",
    [
        (Q.match_all(), "true"),
        (Q.match_none(), "false"),
        (~Q.match_all(), "false"),
        (Q(Q.match_none()), "false"),
        (~Q(Q.match_none()), "true"),
    ],
)
def test_constants_compile_to_literal_truth_values(q, expected):
    from sqlalchemy.dialects import postgresql

    from zeeb_orm import Model, fields
    from zeeb_orm.query.queryset import q_to_condition

    class QcThing(Model):
        name = fields.CharField(max_length=10)

        class Meta:
            table_name = "qc_things"

    cond = q_to_condition(QcThing, q)
    assert cond is not None
    sql = str(cond.compile(dialect=postgresql.dialect())).lower()
    assert sql == expected
