"""Which tools an agent should see by default.

``zeeb_agents`` exports well over a hundred callable tools.  Exposing all of
them at once is a selection problem, not a capability win: an agent facing the
full list reaches for low-level call sequences (``create_model`` →
``create_serializer`` → ``create_viewset`` → ``register_route`` →
``make_migrations``) where one declarative :func:`~zeeb_agents.intent.build_feature`
call would do, and every extra step is a place to get the wiring wrong.

This module is the single source of truth for the split.  It is pure data with
no imports from the rest of the package, so anything may import it — including
:mod:`zeeb_agents.capabilities` and an out-of-process MCP server deciding which
tools to register.

Three tiers:

``core``
    The default agent surface.  Declarative intent tools, the feature
    lifecycle, and the verification loop.  These handle the overwhelming
    majority of work.

``escape_hatch``
    Precise, general-purpose control for the cases a spec cannot express.
    Safe to expose, but an agent that reaches for these first is usually
    solving the wrong problem.

``advanced``
    Everything else — every per-object tool (``create_model``, ``add_field``,
    ``create_viewset``, …).  Implicit: any exported tool not named below.
    These are **not deprecated**.  They remain importable, they remain the
    implementation the feature compiler executes, and calling one directly
    behaves exactly as it always has.  They are simply not the surface an
    agent should be choosing from.

Nothing here removes or renames anything.  Reducing the surface is a
presentation decision, taken by whoever registers the tools.
"""

from __future__ import annotations

#: The default agent surface — declarative intent, feature lifecycle, verification.
CORE_TOOLS: frozenset[str] = frozenset(
    {
        # Discovery — where am I, what can I do, what should I read.
        "get_started",
        "describe_project",
        "list_capabilities",
        "get_resource",
        # Project lifecycle.
        "create_project",
        "bootstrap_project",
        "configure_auth",
        # Feature lifecycle — the primary way capability is added and evolved.
        "plan_feature",
        "build_feature",
        "apply_plan",
        "change_feature",
        "list_features",
        "delete_feature",
        "deactivate_feature",
        "activate_feature",
        # Schema and test operations.
        "make_migrations",
        "run_migrations",
        "run_tests",
        # The acceptance gate and its debugger.
        "verify_project",
        "diagnose_problem",
    }
)

#: Precise control for what a spec cannot express. Opt-in, never the first reach.
ESCAPE_HATCH_TOOLS: frozenset[str] = frozenset(
    {
        "read_file",
        "write_file",
        "search_code",
        "run_query",
        "run_management_command",
    }
)

#: Tier names in presentation order.
TIER_ORDER: tuple[str, ...] = ("core", "escape_hatch", "advanced")

#: The tools an agent surface should register by default.
DEFAULT_SURFACE: frozenset[str] = CORE_TOOLS | ESCAPE_HATCH_TOOLS


def tier_for(name: str) -> str:
    """Return the tier of tool *name* — ``core``, ``escape_hatch``, or ``advanced``.

    Unknown names fall through to ``advanced``: the tier lists name the small,
    curated sets, and everything else is advanced by construction, so a tool
    added to the package without being tiered is never silently promoted.
    """
    if name in CORE_TOOLS:
        return "core"
    if name in ESCAPE_HATCH_TOOLS:
        return "escape_hatch"
    return "advanced"
