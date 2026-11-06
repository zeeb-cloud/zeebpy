"""The skill a generated project ships, verified against the code it describes.

A skill is trusted harder than data: an agent copies a class name, a lookup or
a command out of it without checking. So every backticked name in the skill
has to resolve against the real thing — a path in a scaffolded project, a
registered command and its flags, a field class, a lookup, a QuerySet method,
a ``zeeb_api`` export, a setting, an environment key, an error code, a
``zeeb_agents`` function and its parameters. A token no registry knows fails
the suite with its file and line. The registries are built from the code, not
written down here; the only hand-written allowances are the handful of
non-identifiers listed in ``ALLOWED_VERBATIM`` with their reason.

The promises are run too: the bundled runner script is executed against the
scaffolded project, and the FeatureSpec example in the lifecycle reference has
to pass ``plan_feature`` with no problems.
"""

from __future__ import annotations

import ast
import builtins
import importlib
import inspect
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from zeeb_orm.cli.commands.startproject import run_startproject
from zeeb_orm.cli.main import COMMANDS, build_parser
from zeeb_orm.scaffold import agent_guide, skill
from zeeb_orm.scaffold import project as project_templates

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

PROJECT_NAME = "skill_api"

#: Non-identifier tokens that cannot be resolved against code, with the reason.
ALLOWED_VERBATIM: dict[str, str] = {
    "null": "JSON literal, describing a value in the result envelope",
    "pytest": "the test runner; the loop it belongs to is run in test_scaffold_agent_docs",
}

#: Names a code example may use for the thing it is about: a model instance, a
#: request, a serializer. Only the methods called on them are verified.
EXAMPLE_INSTANCES = {"post", "user", "tag", "self", "request", "serializer", "data"}

#: Example model and class names the prose refers to, as roots of attribute
#: chains (``Post.DoesNotExist``). The attribute is still verified.
EXAMPLE_CLASSES = {"Post", "Author", "Product", "Comment", "Category", "Order", "Tag"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    base = tmp_path_factory.mktemp("skill")
    assert run_startproject(PROJECT_NAME, str(base)) == 0
    return base / PROJECT_NAME


@pytest.fixture(scope="module")
def skill_files(project) -> dict[str, str]:
    """Every file the skill renders, read back from the scaffolded project."""
    files = {}
    for relative in skill.render_skill_files(PROJECT_NAME):
        assert (project / relative).is_file(), f"{relative} was not written"
        files[relative] = (project / relative).read_text()
    return files


@pytest.fixture(scope="module")
def markdown(skill_files) -> dict[str, str]:
    return {path: text for path, text in skill_files.items() if path.endswith(".md")}


@pytest.fixture(scope="module")
def skill_md(skill_files) -> str:
    return skill_files[f"{skill.SKILL_DIR}/SKILL.md"]


def frontmatter(text: str) -> dict[str, str]:
    """The YAML-ish frontmatter as a flat dict (keys are simple, values folded)."""
    assert text.startswith("---\n")
    block = text[4:].split("\n---\n", 1)[0]
    fields: dict[str, str] = {}
    key = None
    for line in block.splitlines():
        if line.startswith(" ") and key:
            fields[key] = (fields[key] + " " + line.strip()).strip()
        else:
            key, _, value = line.partition(":")
            key = key.strip()
            fields[key] = value.strip().lstrip(">-").strip()
    return fields


def strip_fences(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Split markdown into prose and its fenced blocks (language, body)."""
    blocks = re.findall(r"```(\w*)\n(.*?)```", text, re.DOTALL)
    prose = re.sub(r"```\w*\n.*?```", "", text, flags=re.DOTALL)
    return prose, blocks


def inline_tokens(prose: str) -> list[tuple[int, str]]:
    """Every single-backtick span with its line number."""
    found = []
    for number, line in enumerate(prose.splitlines(), start=1):
        for match in re.finditer(r"`([^`\n]+)`", line):
            found.append((number, match.group(1)))
    return found


# ---------------------------------------------------------------------------
# Structure and format
# ---------------------------------------------------------------------------


def test_both_entry_points_carry_the_same_skill_md(skill_files):
    canonical = skill_files[f"{skill.SKILL_DIR}/SKILL.md"]
    mirror = skill_files[f"{skill.CLAUDE_SKILL_DIR}/SKILL.md"]
    assert canonical == mirror


def test_startproject_reports_the_skill_files(tmp_path, capsys):
    assert run_startproject("json_api", str(tmp_path), json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    written = set(payload["data"]["files"])
    assert set(skill.render_skill_files("json_api")) <= written


def test_frontmatter_follows_the_agent_skills_spec(skill_md):
    meta = frontmatter(skill_md)
    name = meta["name"]
    assert name == Path(skill.SKILL_DIR).name == Path(skill.CLAUDE_SKILL_DIR).name
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name) and len(name) <= 64
    description = meta["description"]
    assert 1 <= len(description) <= 1024
    for keyword in ("model", "serializer", "viewset", "migration", "manage.py"):
        assert keyword in description, f"the description no longer triggers on {keyword}"
    assert len(skill_md.splitlines()) < 500


def test_no_placeholder_survives_rendering(skill_files):
    for path, text in skill_files.items():
        assert agent_guide.PROJECT_PLACEHOLDER not in text, path
        assert not re.search(r"@[A-Z_]+@", text), path


def test_the_reference_index_matches_the_directory(project, skill_md):
    on_disk = {p.name for p in (project / skill.SKILL_DIR / "references").glob("*.md")}
    named = set(re.findall(rf"`{re.escape(skill.SKILL_DIR)}/references/([\w-]+\.md)`", skill_md))
    assert on_disk == named == set(skill.SKILL_REFERENCES)


def test_the_skill_promises_the_same_verification_loop_as_agents_md(project, skill_md):
    agents_md = (project / "AGENTS.md").read_text()
    loop = agents_md.split("## Verifying a change", 1)[1].split("```bash\n")[1].split("```")[0]
    assert loop in skill_md


def test_agents_md_points_at_the_skill(project):
    agents_md = (project / "AGENTS.md").read_text()
    assert f"`{skill.SKILL_DIR}/SKILL.md`" in agents_md
    assert ".claude/skills/" in agents_md


def test_the_gitignore_keeps_the_skill(project):
    ignored = {line.strip() for line in (project / ".gitignore").read_text().splitlines()}
    assert not ignored & {".agents", ".agents/", ".claude", ".claude/"}
    assert ".agents/" in project_templates.GITIGNORE


# ---------------------------------------------------------------------------
# The registries: everything the skill may name, read off the code
# ---------------------------------------------------------------------------


def _public(obj) -> set[str]:
    return {n for n in dir(obj) if not n.startswith("_")}


def _all(module_name: str) -> set[str]:
    return set(importlib.import_module(module_name).__all__)


def _params(callable_) -> set[str]:
    return set(inspect.signature(callable_).parameters)


def _string_literals(module, *, keys_only: bool = False) -> set[str]:
    """Identifier-like string constants in a module's source.

    With ``keys_only`` only dict keys and ``getattr``/``hasattr``/``setattr``
    names count — the shape a documented data key or declared optional
    attribute has in code.
    """
    tree = ast.parse(Path(module.__file__).read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if keys_only:
            candidates: list[ast.AST] = []
            if isinstance(node, ast.Dict):
                candidates = [k for k in node.keys if k is not None]
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"getattr", "hasattr", "setattr"}
                and len(node.args) >= 2
            ):
                candidates = [node.args[1]]
            elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
                candidates = [node.slice]
            elif isinstance(node, ast.Compare):
                candidates = [c for c in node.comparators if isinstance(c, ast.Constant)]
            elif isinstance(node, ast.Assign) and len(node.targets) == 1:
                # ``scope_attr = "throttle_scope"``: an attribute name a class
                # declares it reads off a view.
                target = node.targets[0]
                if isinstance(target, ast.Name) and target.id.endswith(("_attr", "_param")):
                    candidates = [node.value]
        else:
            candidates = [node] if isinstance(node, ast.Constant) else []
        for candidate in candidates:
            if isinstance(candidate, ast.Constant) and isinstance(candidate.value, str):
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", candidate.value):
                    found.add(candidate.value)
    return found


def _package_literals(package, **kwargs) -> set[str]:
    found: set[str] = set()
    root = Path(package.__file__).parent
    for path in root.rglob("*.py"):
        module_name = package.__name__ + "." + ".".join(
            path.relative_to(root).with_suffix("").parts
        )
        module_name = module_name.removesuffix(".__init__")
        try:
            module = importlib.import_module(module_name)
        except Exception:  # an optional extra that is not installed
            continue
        if getattr(module, "__file__", None):
            found |= _string_literals(module, **kwargs)
    return found


class Registry:
    """What the skill may refer to, each set traceable to the code it names."""

    def __init__(self, project: Path) -> None:
        import fastapi

        import zeeb_agents
        import zeeb_api
        import zeeb_orm
        import zeeb_orm.db.transaction as transaction
        import zeeb_orm.exceptions as orm_exceptions
        from zeeb_agents import feature_spec, tiers
        from zeeb_agents._utils.code_gen import KNOWN_META_KEYS
        from zeeb_agents._utils.errors import ERROR_CODES
        from zeeb_agents._utils.field_types import FIELD_TYPE_MAP
        from zeeb_api import exceptions as api_exceptions
        from zeeb_api.auth import create_auth_router
        from zeeb_api.filters import OrderingFilter, SearchFilter
        from zeeb_api.pagination import LimitOffsetPagination, PageNumberPagination
        from zeeb_api.permissions import BasePermission
        from zeeb_api.serializers import ModelSerializer, Serializer
        from zeeb_api.viewsets import ModelViewSet, action, extend_schema
        from zeeb_orm.cli import output as cli_output
        from zeeb_orm.models import fields as orm_fields
        from zeeb_orm.models.manager import Manager
        from zeeb_orm.models.related_m2m import ManyRelatedManager
        from zeeb_orm.query.q import LOOKUP_EXPRESSIONS
        from zeeb_orm.query.queryset import QuerySet
        from zeeb_orm.query.transforms import DATETIME_TRANSFORMS

        self.project = project
        self.commands = set(COMMANDS)
        choices = build_parser()._subparsers._group_actions[0].choices
        self.options = {
            command: {o for a in parser._actions for o in a.option_strings}
            for command, parser in choices.items()
        }
        self.lookups = set(LOOKUP_EXPRESSIONS) | set(DATETIME_TRANSFORMS)

        # Classes and modules: anything Capitalized must come from here.
        self.classes = {n for n in _all("zeeb_orm") if n[:1].isupper()}
        self.classes |= {n for n in _all("zeeb_api") if n[:1].isupper()}
        for module_name in (
            "zeeb_api.permissions",
            "zeeb_api.throttling",
            "zeeb_api.pagination",
            "zeeb_api.versioning",
            "zeeb_api.filters",
            "zeeb_api.serializers",
        ):
            self.classes |= {n for n in _all(module_name) if n[:1].isupper()}
        self.classes |= {
            n for n, o in vars(api_exceptions).items() if inspect.isclass(o) and n[:1].isupper()
        }
        self.classes |= {
            n for n, o in vars(orm_exceptions).items() if inspect.isclass(o) and n[:1].isupper()
        }
        self.classes |= {n for n in _all("zeeb_orm.validators") if n[:1].isupper()}
        self.classes |= {n for n in dir(fastapi) if n[:1].isupper()}
        self.classes |= {"Meta"}  # the inner options class on models and serializers
        self.classes |= {m.upper() for m in feature_spec._FUNCTION_METHODS}  # HTTP methods
        self.objects = {
            "zeeb_orm": zeeb_orm,
            "zeeb_api": zeeb_api,
            "zeeb_agents": zeeb_agents,
            "ErrorCode": api_exceptions.ErrorCode,
            "Manager": Manager,
            "QuerySet": QuerySet,
        }

        # Lowercase names: functions, methods, attributes, keyword arguments,
        # keys, values of closed vocabularies.
        lower: set[str] = set()
        lower |= {n for n in _all("zeeb_orm") if n[:1].islower()}
        lower |= {n for n in _all("zeeb_api") if n[:1].islower()}
        lower |= _all("zeeb_api.conf.env") | _all("zeeb_api.throttling") | _all("zeeb_api.filters")
        lower |= {n for n in _all("zeeb_api.versioning") if n[:1].islower()}
        lower |= _public(transaction) | _public(zeeb_orm.signals)
        lower |= _public(QuerySet) | _public(Manager) | _public(zeeb_orm.Model)
        lower |= _public(ManyRelatedManager)
        lower |= _public(ModelViewSet) | _public(ModelSerializer) | _public(Serializer)
        lower |= _public(BasePermission) | _public(zeeb_api.DefaultRouter)
        lower |= _public(zeeb_api.AbstractUser)
        lower |= {n for n in _public(orm_exceptions.ValidationError) if n.islower()}
        lower |= _params(action) | _params(extend_schema) | _params(Serializer.__init__)
        lower |= _params(zeeb_api.DefaultRouter.register)
        lower |= _params(zeeb_orm.Model.save) | _params(QuerySet.bulk_create)
        for field_class in (getattr(orm_fields, n, None) for n in _all("zeeb_orm")):
            if inspect.isclass(field_class) and issubclass(field_class, orm_fields.Field):
                lower |= _params(field_class.__init__)
        lower |= set(KNOWN_META_KEYS) | set(ModelSerializer.EXTRA_KWARGS_OPTIONS)
        lower |= _string_literals(sys.modules[ModelSerializer.__module__], keys_only=True)
        lower |= set(zeeb_api.QueryRequest.model_fields)
        lower |= self.commands
        lower |= {n for n in zeeb_agents.__all__ if callable(getattr(zeeb_agents, n))}
        for name in zeeb_agents.__all__:
            function = getattr(zeeb_agents, name)
            if callable(function):
                lower |= _params(function)
                # The documented ``data`` keys: the MCP-facing contract.
                doc = inspect.getdoc(function) or ""
                lower |= set(re.findall(r"^\s+(\w+) \(", doc, re.M))
                lower |= set(re.findall(r"`(\w+)`", doc)) | set(re.findall(r'"(\w+)"', doc))
        lower |= set(ERROR_CODES) | set(cli_output.CLI_ERROR_CODES)
        lower |= set(FIELD_TYPE_MAP) | set(feature_spec.FUNCTION_KINDS)
        lower |= set(feature_spec.AUTHENTICATION_MAP) | set(feature_spec.ACTOR_PERMISSION_MAP)
        lower |= set(feature_spec._SPEC_KEYS) | set(feature_spec._ENTITY_KEYS)
        lower |= set(feature_spec._FUNCTION_KEYS) | set(feature_spec._API_DEFAULTS)
        lower |= set(tiers.tier_for(n) for n in zeeb_agents.__all__)
        # Documented data keys, declared optional attributes, change operations:
        # string literals in the code that returns or reads them.
        lower |= _package_literals(zeeb_agents)
        lower |= _string_literals(cli_output)
        lower |= _package_literals(zeeb_api, keys_only=True)
        # Names the generated project itself defines.
        for module_path in (f"{PROJECT_NAME}/urls.py", "tests/conftest.py"):
            tree = ast.parse((project / module_path).read_text())
            lower |= {
                node.name
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            lower |= {
                target.id
                for node in tree.body
                if isinstance(node, ast.Assign)
                for target in node.targets
                if isinstance(target, ast.Name)
            }
        self.lower = lower

        # UPPER_CASE: settings, environment keys, error codes, on_delete.
        settings_py = project_templates.SETTINGS_PY
        upper: set[str] = set(re.findall(r"^([A-Z][A-Z0-9_]+) *=", settings_py, re.M))
        env_keys = re.findall(r"^#? ?([A-Z][A-Z0-9_]+)=(.*)$", project_templates.ENV_EXAMPLE, re.M)
        upper |= {key for key, _ in env_keys}
        settings_doc = Path("docs/configuration/settings.md").read_text()
        upper |= set(re.findall(r"`([A-Z][A-Z0-9_]+)`", settings_doc))
        upper |= {n for n in _all("zeeb_orm") if n.isupper()}
        upper |= {n for n in dir(api_exceptions.ErrorCode) if n.isupper()}
        upper |= {n for n in _package_literals(zeeb_api) if n.isupper() and len(n) > 3}
        self.upper = upper

        # Values that are not identifiers: env example values, cardinalities.
        self.raw = {value.split("#")[0].strip() for _, value in env_keys} - {""}
        self.raw |= set(feature_spec.CARDINALITY_MAP)
        self.rate = re.compile(r"\d+/(s|sec|m|min|h|hour|d|day)")

        # Routes the project serves without a line of app code.
        self.routes = {route.path for route in create_auth_router().routes}
        self.query_params = {
            SearchFilter.search_param,
            OrderingFilter.ordering_param,
            PageNumberPagination.page_query_param,
            PageNumberPagination.page_size_query_param,
            LimitOffsetPagination.limit_query_param,
            LimitOffsetPagination.offset_query_param,
        }
        # Paths the project grows into (the feature manifest and archive).
        self.future_paths = {
            literal
            for literal in _package_literals(zeeb_agents) | {".zeeb", ".zeeb/features.json"}
        }
        manifest_source = Path(zeeb_agents.feature_manifest.__file__).read_text()
        self.future_paths |= set(re.findall(r'"(\.zeeb[\w./]*)"', manifest_source))
        self.builtins = set(dir(builtins))
        self.project_apps = {p.name for p in (project / "apps").iterdir() if p.is_dir()}

    # -- resolution -------------------------------------------------------

    def resolve(self, token: str) -> str | None:
        """Return ``None`` when *token* is verified, else why it is not."""
        if token in ALLOWED_VERBATIM:
            return None
        if "<" in token and ">" in token:
            return None  # a placeholder such as <app> or <name>_id
        for check in (
            self._route,
            self._path,
            self._command,
            self._option,
            self._lookup,
            self._upper_assignment,
            self._query_param,
            self._raw_value,
            self._expression,
        ):
            claimed, problem = check(token)
            if claimed:
                return problem
        return "no registry knows this token"

    def _path(self, token: str) -> tuple[bool, str | None]:
        file_name = re.fullmatch(r"[\w.-]+\.(py|md|toml|ini|json|example)", token)
        dotfile = re.fullmatch(r"\.[\w.-]+", token)
        if not ("/" in token or file_name or dotfile):
            return False, None
        if token.startswith(("python ", "manage.py", "?", "/", '"', "'")):
            return False, None  # a command, a query parameter, a route, a literal
        candidate = token.rstrip("/")
        if (self.project / candidate).exists():
            return True, None
        if "/" not in candidate and any(self.project.rglob(candidate)):
            return True, None
        if candidate in self.future_paths or candidate.rstrip("/") in self.future_paths:
            return True, None
        return True, "no such path in a scaffolded project"

    def _command(self, token: str) -> tuple[bool, str | None]:
        match = re.match(r"(?:python )?manage\.py ([a-z-]+)(.*)", token)
        if not match:
            return False, None
        command, rest = match.groups()
        if command not in self.commands:
            return True, f"manage.py {command} is not a registered command"
        for flag in re.findall(r"--[a-z-]+", rest):
            if flag not in self.options[command]:
                return True, f"{flag} is not an option of manage.py {command}"
        return True, None

    def _option(self, token: str) -> tuple[bool, str | None]:
        if not re.fullmatch(r"--[a-z-]+", token):
            return False, None
        if any(token in options for options in self.options.values()):
            return True, None
        return True, "no management command takes this option"

    def _lookup(self, token: str) -> tuple[bool, str | None]:
        match = re.fullmatch(r"__([a-z_]+)", token)
        if not match:
            return False, None
        if match.group(1) in self.lookups:
            return True, None
        return True, "not a lookup or datetime transform"

    def _upper_assignment(self, token: str) -> tuple[bool, str | None]:
        match = re.fullmatch(r"([A-Z][A-Z0-9_]+)(?:=.*)?", token)
        if not match:
            return False, None
        if match.group(1) in self.upper:
            return True, None
        return True, "not a setting, environment key, error code or constant"

    def _route(self, token: str) -> tuple[bool, str | None]:
        match = re.fullmatch(r"(?:(?:GET|POST|PUT|PATCH|DELETE) )?(/[\w/{}.-]+)", token)
        if not match:
            return False, None
        if match.group(1) in self.routes:
            return True, None
        return True, "not a route the generated project serves"

    def _query_param(self, token: str) -> tuple[bool, str | None]:
        match = re.fullmatch(r"\?(\w+)=", token)
        if not match:
            return False, None
        if match.group(1) in self.query_params:
            return True, None
        return True, "not a query parameter a backend or paginator reads"

    def _raw_value(self, token: str) -> tuple[bool, str | None]:
        if token in self.raw:
            return True, None
        return False, None

    def _expression(self, token: str) -> tuple[bool, str | None]:
        tree = self._parse(token)
        if tree is None:
            return True, "not a Python expression, and no other registry claims it"
        # The value compared by a lookup is the caller's data, not a name.
        free_values = {
            id(node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.keyword) and node.arg and "__" in node.arg
        }
        problems = [
            p
            for node in ast.walk(tree)
            if id(node) not in free_values
            for p in self._check_node(node)
        ]
        return True, "; ".join(sorted(set(problems))) or None

    @staticmethod
    def _parse(token: str) -> ast.AST | None:
        wrapped = (f"async def _():\n    return {token}", f"async def _():\n    {token}")
        for source in (token, *wrapped):
            try:
                return ast.parse(source)
            except SyntaxError:
                continue
        return None

    def _check_node(self, node: ast.AST) -> list[str]:
        if isinstance(node, ast.Attribute):
            return self._check_attribute(node)
        if isinstance(node, ast.Name):
            return self._check_name(node.id)
        if isinstance(node, ast.keyword) and node.arg:
            return self._check_keyword(node.arg)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return self._check_literal(node.value)
        return []

    def _check_name(self, name: str) -> list[str]:
        if name in self.classes or name in self.lower or name in self.upper:
            return []
        if name in EXAMPLE_INSTANCES or name in EXAMPLE_CLASSES or name in self.builtins:
            return []
        if name == "_":
            return []
        try:
            importlib.import_module(name)
        except ImportError:
            return [f"unknown name {name}"]
        return []

    def _check_attribute(self, node: ast.Attribute) -> list[str]:
        chain = []
        current: ast.AST = node
        while isinstance(current, ast.Attribute):
            chain.append(current.attr)
            current = current.value
        if not isinstance(current, ast.Name):
            return []  # the root is a call or a literal; its own node is checked
        root = current.id
        chain.reverse()
        dotted = ".".join([root, *chain])
        # A real object: walk it.
        if root in self.objects or root in sys.modules or self._importable(root):
            target = self.objects.get(root) or importlib.import_module(root)
            for attr in chain:
                try:
                    target = getattr(target, attr)
                except AttributeError:
                    try:
                        target = importlib.import_module(f"{dotted.split(attr)[0]}{attr}")
                    except ImportError:
                        return [f"{dotted}: no attribute {attr}"]
            return []
        if root in EXAMPLE_INSTANCES:
            return []  # only calls on it are verified, through their Name/keyword nodes
        attr = node.attr
        if attr in self.classes or attr in self.lower or attr in self.upper:
            return []
        return [f"unknown attribute {attr} in {dotted}"]

    @staticmethod
    def _importable(name: str) -> bool:
        try:
            importlib.import_module(name)
        except ImportError:
            return False
        return True

    def _check_keyword(self, name: str) -> list[str]:
        if "__" in name:
            if name.rsplit("__", 1)[1] in self.lookups:
                return []
            return [f"{name} does not end in a lookup"]
        if name in self.lower:
            return []
        return [f"unknown keyword argument {name}"]

    def _check_literal(self, value: str) -> list[str]:
        if value == "__all__" or self.rate.fullmatch(value) or value in self.raw:
            return []
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            return self._check_name(value)
        match = re.fullmatch(r"(\w+)\.(\w+)", value)
        if match:
            app, model = match.groups()
            if app in self.project_apps:
                models = ast.parse((self.project / "apps" / app / "models.py").read_text())
                if any(isinstance(n, ast.ClassDef) and n.name == model for n in models.body):
                    return []
            if app == "app" and model == "Model":
                return []  # the generic label pattern
            return [f"{value}: no such model in a scaffolded project"]
        return [f"unverifiable string literal {value!r}"]


@pytest.fixture(scope="module")
def registry(project) -> Registry:
    return Registry(project)


# ---------------------------------------------------------------------------
# The rule: every backticked name resolves
# ---------------------------------------------------------------------------


def test_every_backticked_name_resolves(markdown, registry):
    failures = []
    for path, text in markdown.items():
        prose, _ = strip_fences(text)
        for line, token in inline_tokens(prose):
            problem = registry.resolve(token)
            if problem:
                failures.append(f"{path}:{line}: `{token}` — {problem}")
    assert not failures, "unverified names in the skill:\n" + "\n".join(failures)


def test_every_allowance_is_still_used(markdown):
    """An allowance nobody uses is a hole waiting for a future token."""
    used = set()
    for text in markdown.values():
        prose, _ = strip_fences(text)
        used |= {token for _, token in inline_tokens(prose)}
    unused = set(ALLOWED_VERBATIM) - used
    assert not unused, f"remove the stale allowances: {sorted(unused)}"


def test_every_markdown_table_is_well_formed(markdown):
    for path, text in markdown.items():
        prose, _ = strip_fences(text)
        for number, line in enumerate(prose.splitlines(), start=1):
            if line.startswith("|"):
                assert line.endswith("|"), f"{path}:{number}: table row is not closed"


# ---------------------------------------------------------------------------
# Fenced blocks: they are meant to be copied
# ---------------------------------------------------------------------------


def test_every_python_block_compiles(markdown):
    for path, text in markdown.items():
        _, blocks = strip_fences(text)
        for index, (language, body) in enumerate(blocks):
            if language == "python":
                # Snippets show awaited calls as they appear inside an async
                # handler, so top-level await is allowed.
                compile(body, f"{path}:block{index}.py", "exec", ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)


def test_every_bash_block_names_real_commands(markdown, registry):
    import zeeb_agents

    for path, text in markdown.items():
        _, blocks = strip_fences(text)
        for language, body in blocks:
            if language != "bash":
                continue
            for line in body.splitlines():
                command = line.split("#")[0].strip()
                if not command:
                    continue
                if "manage.py" in command:
                    assert registry.resolve(command) is None, f"{path}: {command}"
                elif "zeeb_agent.py" in command:
                    function = command.split("zeeb_agent.py", 1)[1].split()[0]
                    assert function in zeeb_agents.__all__, f"{path}: {command}"
                else:
                    assert command.split()[0] in {"pytest", "ruff", "pip"}, f"{path}: {command}"


def test_every_json_block_parses(markdown):
    for path, text in markdown.items():
        _, blocks = strip_fences(text)
        for language, body in blocks:
            if language == "json":
                json.loads(body)


# ---------------------------------------------------------------------------
# The promises hold: the runner runs, the spec compiles, the changes are known
# ---------------------------------------------------------------------------


def lifecycle_blocks(markdown) -> list[dict]:
    text = markdown[f"{skill.SKILL_DIR}/references/lifecycle.md"]
    _, blocks = strip_fences(text)
    return [json.loads(body) for language, body in blocks if language == "json"]


def run_agent(project: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(Path.cwd()), LOG_LEVEL="CRITICAL")
    script = project / skill.SKILL_DIR / "scripts" / "zeeb_agent.py"
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=project, env=env, capture_output=True, text=True, timeout=300,
    )


def test_the_runner_script_describes_the_project(project):
    completed = run_agent(project, "describe_project")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["success"] is True
    assert "accounts" in {app["name"] for app in payload["data"]["apps"]}


def test_the_runner_script_refuses_bad_input(project):
    assert run_agent(project, "no_such_function").returncode == 2
    assert run_agent(project, "describe_project", "not json").returncode == 2
    assert run_agent(project, "describe_project", "[1]").returncode == 2
    assert run_agent(project).returncode == 2


def test_the_feature_spec_example_plans_cleanly(project, markdown):
    spec_block, *_ = lifecycle_blocks(markdown)
    completed = run_agent(project, "plan_feature", json.dumps(spec_block))
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["success"] is True, payload["message"]
    assert not payload["data"].get("problems")
    assert (project / ".zeeb").exists() is False, "plan_feature must write nothing"


def test_the_change_example_uses_known_operations(markdown):
    import zeeb_agents
    import zeeb_agents.feature_spec as feature_spec

    pattern = re.compile(r"(?:add|remove|set|alter)_\w+")
    # An operation is real when the compiler's source carries it as a literal
    # and change_feature's docstring documents it.
    in_code = {lit for lit in _string_literals(feature_spec) if pattern.fullmatch(lit)}
    documented = set(pattern.findall(inspect.getdoc(zeeb_agents.change_feature) or ""))
    known = in_code & documented
    _, changes_block, *_ = lifecycle_blocks(markdown)
    for change in changes_block["changes"]:
        assert change["operation"] in known, change["operation"]
    text = markdown[f"{skill.SKILL_DIR}/references/lifecycle.md"]
    for named in re.findall(r"`((?:add|remove|set|alter)_\w+)`", text):
        assert named in known, f"lifecycle.md names an operation the compiler lacks: {named}"
