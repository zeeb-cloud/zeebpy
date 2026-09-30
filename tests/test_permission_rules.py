"""Rule -> Q compilation: queryset scoping must agree with the object check.

The object-level ``Rule.check`` is the oracle. For every SQL-expressible rule
and every kind of user, ``readable_by(user)`` must return exactly the objects
``check`` grants. Rules that cannot be compiled (``Rule.custom``) must fail
closed: the queryset never returns an object the check would refuse.

The rules used to compile "match everything" to an empty ``Q()``, which is a
no-op under ``|`` and ``~`` — so ``~Rule.staff()`` for a staff user and
``~Rule.public()`` returned every row, ``staff | owner`` for a staff user
collapsed to owner-only, and ``Rule.custom`` left querysets unfiltered.
"""

from types import SimpleNamespace

import pytest

from zeeb_orm import Model, Q, close_all_connections, configure, fields, setup_database
from zeeb_orm.permissions import Rule


class PrOwner(Model):
    name = fields.CharField(max_length=50)

    class Meta:
        table_name = "pr_owners"


class PrDoc(Model):
    title = fields.CharField(max_length=50)
    author = fields.ForeignKey(PrOwner, null=True, on_delete="SET_NULL", related_name="docs")
    status = fields.CharField(max_length=20, null=True)

    class Meta:
        table_name = "pr_docs"


MODELS = (PrOwner, PrDoc)


@pytest.fixture
async def db():
    from zeeb_orm.conf.settings import Settings
    from zeeb_orm.models.base import metadata

    Settings.reset()
    for model in MODELS:
        model._sa_table = None
        model._sa_model = None
    metadata.clear()
    configure(database={"url": "sqlite+aiosqlite:///:memory:"})
    database = await setup_database("sqlite+aiosqlite:///:memory:")
    for model in MODELS:
        model._get_table()
    await database.create_all()
    yield database
    await database.drop_all()
    await close_all_connections()
    for model in MODELS:
        table = metadata.tables.get(model._meta.db_table)
        if table is not None:
            metadata.remove(table)
        model._sa_table = None
        model._sa_model = None
    Settings.reset()


@pytest.fixture
async def world(db):
    owner = await PrOwner.objects.create(name="owner")
    other = await PrOwner.objects.create(name="other")
    staff = await PrOwner.objects.create(name="staff")
    docs = [
        await PrDoc.objects.create(title="own-published", author=owner, status="published"),
        await PrDoc.objects.create(title="own-draft", author=owner, status="draft"),
        await PrDoc.objects.create(title="other-published", author=other, status="published"),
        await PrDoc.objects.create(title="other-draft", author=other, status="draft"),
        await PrDoc.objects.create(title="orphan-published", author=None, status="published"),
        await PrDoc.objects.create(title="orphan-nostatus", author=None, status=None),
    ]
    users = {
        "staff": SimpleNamespace(id=staff.id, is_staff=True, is_superuser=False),
        "superuser": SimpleNamespace(id=staff.id, is_staff=False, is_superuser=True),
        "owner": SimpleNamespace(id=owner.id, is_staff=False, is_superuser=False),
        "other": SimpleNamespace(id=other.id, is_staff=False, is_superuser=False),
        "anonymous": None,
    }
    return {"docs": docs, "users": users}


def _rules() -> dict[str, Rule]:
    staff = Rule.staff()
    owner = Rule.owner("author")
    public = Rule.public()
    published = Rule.Q(status="published")
    return {
        "public": public,
        "~public": ~public,
        "staff": staff,
        "~staff": ~staff,
        "superuser": Rule.superuser(),
        "~superuser": ~Rule.superuser(),
        "authenticated": Rule.authenticated(),
        "~authenticated": ~Rule.authenticated(),
        "owner": owner,
        "~owner": ~owner,
        "published": published,
        "~published": ~published,
        "empty Rule.Q()": Rule.Q(),
        "~empty Rule.Q()": ~Rule.Q(),
        "staff | owner": staff | owner,
        "owner | staff": owner | staff,
        "staff & owner": staff & owner,
        "~(staff | owner)": ~(staff | owner),
        "~(staff & owner)": ~(staff & owner),
        "~staff | owner": ~staff | owner,
        "~staff & owner": ~staff & owner,
        "published | owner | staff": published | owner | staff,
        "authenticated & (owner | staff)": Rule.authenticated() & (owner | staff),
        "~(published & ~owner)": ~(published & ~owner),
        "public & ~staff": public & ~staff,
        "public | ~public": public | ~public,
        "~(~staff)": ~(~staff),
    }


RULE_NAMES = list(_rules())
USER_NAMES = ["staff", "superuser", "owner", "other", "anonymous"]


async def _granted(rule: Rule, docs, user) -> set[str]:
    return {d.title for d in docs if await rule.check(d, user)}


async def _scoped(rule: Rule, user) -> set[str]:
    rows = await PrDoc.objects.filter(rule.to_q(user, PrDoc))
    return {d.title for d in rows}


@pytest.mark.parametrize("user_name", USER_NAMES)
@pytest.mark.parametrize("rule_name", RULE_NAMES)
async def test_queryset_scope_matches_object_check(world, rule_name, user_name):
    rule = _rules()[rule_name]
    user = world["users"][user_name]
    expected = await _granted(rule, world["docs"], user)
    assert await _scoped(rule, user) == expected


@pytest.mark.parametrize("user_name", USER_NAMES)
async def test_negated_staff_for_staff_matches_nothing(world, user_name):
    scoped = await _scoped(~Rule.staff(), world["users"][user_name])
    if user_name in ("staff", "superuser"):
        assert scoped == set()
    else:
        assert len(scoped) == len(world["docs"])


async def test_staff_or_owner_for_staff_is_everything(world):
    scoped = await _scoped(Rule.staff() | Rule.owner("author"), world["users"]["staff"])
    assert scoped == {d.title for d in world["docs"]}


# Rule.custom cannot compile to SQL: it must fail closed.


async def _always(obj, user):
    return True


async def _never(obj, user):
    return False


def _custom_rules() -> dict[str, Rule]:
    yes = Rule.custom(_always)
    no = Rule.custom(_never)
    staff = Rule.staff()
    owner = Rule.owner("author")
    return {
        "custom(yes)": yes,
        "~custom(no)": ~no,
        "custom(yes) | owner": yes | owner,
        "~custom(no) | owner": ~no | owner,
        "custom(yes) & staff": yes & staff,
        "~(custom(no) & staff)": ~(no & staff),
        "staff | custom(yes)": staff | yes,
        "~(custom(no) | ~owner)": ~(no | ~owner),
    }


@pytest.mark.parametrize("user_name", USER_NAMES)
@pytest.mark.parametrize("rule_name", list(_custom_rules()))
async def test_custom_rules_fail_closed(world, rule_name, user_name):
    rule = _custom_rules()[rule_name]
    user = world["users"][user_name]
    granted = await _granted(rule, world["docs"], user)
    scoped = await _scoped(rule, user)
    # Never more than the object check grants...
    assert scoped <= granted
    # ...and the permissive bound never misses an object the check grants,
    # so candidates_q + check() reaches every granted object.
    candidates = {d.title for d in await PrDoc.objects.filter(rule.candidates_q(user, PrDoc))}
    assert granted <= candidates


async def test_custom_rule_alone_scopes_to_nothing(world):
    user = world["users"]["owner"]
    assert await _scoped(Rule.custom(_always), user) == set()
    assert await _scoped(~Rule.custom(_never), user) == set()


async def test_custom_rule_does_not_hide_sql_expressible_grants(world):
    user = world["users"]["owner"]
    scoped = await _scoped(Rule.custom(_always) | Rule.owner("author"), user)
    assert scoped == {"own-published", "own-draft"}
    assert await _scoped(Rule.staff() | Rule.custom(_never), world["users"]["staff"]) == {
        d.title for d in world["docs"]
    }


async def test_with_permission_uses_the_model_rules(world):
    class PrScoped(Model):
        title = fields.CharField(max_length=50)

        read_permission = ~Rule.staff()

        class Meta:
            table_name = "pr_docs"

    # get_read_filter is generated from the rule: a staff user reads nothing.
    q = PrScoped.get_read_filter(world["users"]["staff"])
    assert q.is_match_none
    assert PrScoped.get_read_filter(None).is_match_all


def test_to_q_constants_for_user_only_rules():
    staff_user = SimpleNamespace(id=1, is_staff=True, is_superuser=False)
    assert Rule.public().to_q(None).is_match_all
    assert (~Rule.public()).to_q(None).is_match_none
    assert Rule.staff().to_q(staff_user).is_match_all
    assert (~Rule.staff()).to_q(staff_user).is_match_none
    assert (Rule.staff() | Rule.owner("author")).to_q(staff_user).is_match_all
    assert Rule.custom(_always).to_q(staff_user).is_match_none
    assert Rule.custom(_always).candidates_q(staff_user).is_match_all
    assert (Rule.owner("author") & ~Rule.public()).to_q(staff_user).is_match_none
    assert isinstance((Rule.owner("author") | ~Rule.public()).to_q(staff_user), Q)
