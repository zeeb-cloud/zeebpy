"""
Safe Q filter expression parser using Python AST.

Parses Q filter strings like:
    "Q(name__icontains='john')"
    "Q(price__gte=10) & Q(price__lte=50)"
    "Q(active=True) | Q(featured=True)"
    "~Q(deleted=True)"
    "Q(status__in=['pending', 'approved'])"

Security:
- Uses ast.parse() - NO eval/exec
- Whitelist of allowed AST node types
- Only Q() calls allowed
- Only |, &, ~ operators allowed
- Values must be safe literals (a number may be negative); lists only for
  __in / __range
- Bounded input: at most QUERY_FILTER_MAX_LENGTH characters and
  MAX_FILTER_DEPTH levels of nesting, checked without recursion, so a
  crafted filter is a QFilterError (400), never a RecursionError (500)
"""

from __future__ import annotations

import ast
from typing import Any

from zeeb_orm.query.q import Q, QOperator


class QFilterError(Exception):
    """Error parsing Q filter expression."""
    pass


# Allowed AST node types
ALLOWED_NODES = {
    ast.Expression,
    ast.Call,
    ast.Name,
    ast.Load,
    ast.keyword,
    ast.Constant,
    ast.List,
    ast.Tuple,
    ast.BinOp,
    ast.BitOr,   # |
    ast.BitAnd,  # &
    ast.UnaryOp,
    ast.Invert,  # ~
    ast.USub,    # - , only in front of a number (see _is_literal)
}

# Allowed constant types
ALLOWED_CONSTANT_TYPES = (str, int, float, bool, type(None))

#: Fallback for the ``QUERY_FILTER_MAX_LENGTH`` setting.
DEFAULT_MAX_LENGTH = 4096

#: Deepest nesting of ``~``, ``Q(...)`` and *alternating* ``&``/``|`` a filter
#: may use. A run of the same operator (``Q(a=1) | Q(a=2) | ...``) is one
#: level, however long: it becomes one flat OR, as ``Q`` itself merges it.
MAX_FILTER_DEPTH = 32

#: ``Q.__init__`` parameters, not field lookups: never accepted from a client.
RESERVED_KEYWORDS = frozenset({"_connector", "_negated"})

#: The only lookups that take a list or tuple, and how many items they need.
_SEQUENCE_LOOKUPS: dict[str, int | None] = {"in": None, "range": 2}


def parse_q_filter(expr: str) -> Q:
    """
    Safely parse a Q filter expression string into a Q object.

    Args:
        expr: Q filter expression string, e.g. "Q(name='john') | Q(active=True)"

    Returns:
        Q object that can be used with QuerySet.filter()

    Raises:
        QFilterError: If expression is invalid or contains disallowed constructs,
            is longer than ``QUERY_FILTER_MAX_LENGTH`` or nested deeper than
            ``MAX_FILTER_DEPTH``

    Examples:
        >>> parse_q_filter("Q(name__icontains='ring')")
        Q(name__icontains='ring')

        >>> parse_q_filter("Q(price__gte=10) & Q(price__lte=50)")
        Q(price__gte=10) & Q(price__lte=50)

        >>> parse_q_filter("~Q(deleted=True)")
        ~Q(deleted=True)
    """
    return parse_q_filter_with_paths(expr)[0]


def parse_q_filter_with_paths(expr: str) -> tuple[Q, set[str]]:
    """Parse *expr* once into its ``Q`` and every keyword path it uses.

    The same validation as :func:`parse_q_filter`; the paths are what
    :func:`extract_q_paths` returns. Check the paths against an allow-list
    before filtering with the ``Q`` — this is what ``POST /query`` does.
    """
    tree = _parse_source(expr)
    if tree is None:
        return Q(), set()
    paths = _validate_ast(tree)
    return _ast_to_q(tree.body), paths


def extract_q_paths(expr: str) -> set[str]:
    """Return every full keyword path a Q filter expression uses.

    ``"Q(author__name__icontains='x') | Q(title='y')"`` gives
    ``{"author__name__icontains", "title"}``. Validate these — not just their
    roots — against an allow-list (see ``zeeb_api.query.paths``): a root check
    lets ``author__password__startswith`` through wherever ``author`` is
    allowed.
    """
    tree = _parse_source(expr)
    if tree is None:
        return set()

    paths: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Q"
        ):
            for kw in node.keywords:
                if kw.arg:
                    paths.add(kw.arg)
    return paths


def extract_q_fields(expr: str) -> set[str]:
    """Return the root field names referenced by a Q filter expression.

    For each ``Q(field__lookup=value)`` keyword the root before the first
    ``__`` is collected. Not sufficient on its own as an allow-list check —
    it ignores everything after the first hop; use :func:`extract_q_paths`.
    """
    return {path.split("__", 1)[0] for path in extract_q_paths(expr)}


def _max_length() -> int:
    from zeeb_api.conf import settings

    return int(getattr(settings, "QUERY_FILTER_MAX_LENGTH", DEFAULT_MAX_LENGTH))


def _parse_source(expr: str) -> ast.Expression | None:
    """*expr* as an AST, or None for an empty filter; bounded in length."""
    if not expr or not expr.strip():
        return None

    expr = expr.strip()
    max_length = _max_length()
    if len(expr) > max_length:
        raise QFilterError(f"Filter is longer than {max_length} characters")

    try:
        return ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise QFilterError(f"Invalid syntax: {e}")
    except (ValueError, RecursionError, MemoryError):
        # A NUL byte (ValueError before Python 3.12) or a parser limit.
        raise QFilterError("Invalid syntax") from None


def _validate_ast(tree: ast.Expression) -> set[str]:
    """Check every node of *tree* without recursing; return its keyword paths.

    The depth counted is the nesting of ``Q`` structure (see
    :data:`MAX_FILTER_DEPTH`), so the check also bounds the recursion in
    :func:`_ast_to_q`.
    """
    paths: set[str] = set()
    stack: list[tuple[ast.AST, int, type | None]] = [(tree, 0, None)]
    while stack:
        node, depth, parent_op = stack.pop()
        if type(node) not in ALLOWED_NODES:
            raise QFilterError(f"Disallowed expression type: {type(node).__name__}")

        # Check Name nodes - only allow 'Q', 'True', 'False', 'None'
        if isinstance(node, ast.Name):
            if node.id not in ('Q', 'True', 'False', 'None'):
                raise QFilterError(f"Disallowed name: {node.id}")

        # Check Constant values
        if isinstance(node, ast.Constant):
            if not isinstance(node.value, ALLOWED_CONSTANT_TYPES):
                raise QFilterError(f"Disallowed constant type: {type(node.value).__name__}")

        if (
            isinstance(node, ast.UnaryOp)
            and isinstance(node.op, ast.USub)
            and not _is_negative_number(node)
        ):
            raise QFilterError("'-' is only allowed in front of a number")

        if isinstance(node, (ast.Call, ast.UnaryOp)) or (
            isinstance(node, ast.BinOp) and type(node.op) is not parent_op
        ):
            depth += 1
            if depth > MAX_FILTER_DEPTH:
                raise QFilterError(f"Filter is nested deeper than {MAX_FILTER_DEPTH} levels")

        # Check Call nodes - only allow Q()
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id != 'Q':
                raise QFilterError("Only Q() calls are allowed")
            # Q() should not have positional args (except other Q objects via combinators)
            for arg in node.args:
                if not isinstance(arg, (ast.Call, ast.BinOp, ast.UnaryOp)):
                    raise QFilterError("Q() positional arguments must be other Q expressions")
            for kw in node.keywords:
                if kw.arg is None:
                    raise QFilterError("**kwargs not allowed in Q()")
                if kw.arg in RESERVED_KEYWORDS:
                    raise QFilterError(f"'{kw.arg}' is not a field lookup")
                _check_value(kw.arg, kw.value)
                paths.add(kw.arg)

        child_op = type(node.op) if isinstance(node, ast.BinOp) else None
        for child in ast.iter_child_nodes(node):
            stack.append((child, depth, child_op))
    return paths


def _check_value(path: str, node: ast.AST) -> None:
    """A lookup value is a literal; a list or tuple exactly where the lookup takes one."""
    lookup = path.rsplit("__", 1)[-1] if "__" in path else "exact"
    if not isinstance(node, (ast.List, ast.Tuple)):
        if not _is_literal(node):
            raise QFilterError(f"Unsupported value for '{path}': {type(node).__name__}")
        if lookup in _SEQUENCE_LOOKUPS:
            raise QFilterError(f"'{path}' takes a list of values")
        return

    if lookup not in _SEQUENCE_LOOKUPS:
        raise QFilterError(f"'{path}' takes a single value, not a list")
    size = _SEQUENCE_LOOKUPS[lookup]
    if size is not None and len(node.elts) != size:
        raise QFilterError(f"'{path}' takes exactly {size} values")
    if not all(_is_literal(elt) for elt in node.elts):
        raise QFilterError(f"'{path}' takes a list of literal values")


def _is_literal(node: ast.AST) -> bool:
    if isinstance(node, ast.UnaryOp):
        return _is_negative_number(node)
    return isinstance(node, ast.Constant) or (
        isinstance(node, ast.Name) and node.id in ("True", "False", "None")
    )


def _is_negative_number(node: ast.UnaryOp) -> bool:
    """``-3`` / ``-1.5``: a minus applied once to a numeric literal."""
    return (
        isinstance(node.op, ast.USub)
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, (int, float))
        and not isinstance(node.operand.value, bool)
    )


def _operands(node: ast.BinOp) -> list[ast.AST]:
    """The operands of a run of the same operator, left to right, without recursing."""
    op = type(node.op)
    operands: list[ast.AST] = []
    stack: list[ast.AST] = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, ast.BinOp) and type(current.op) is op:
            stack.append(current.right)
            stack.append(current.left)
        else:
            operands.append(current)
    return operands


def _ast_to_q(node: ast.AST) -> Q:
    """Convert an AST node to a Q object."""

    # Handle Q() call
    if isinstance(node, ast.Call):
        # Extract keyword arguments
        kwargs = {}
        for kw in node.keywords:
            if kw.arg is None:
                raise QFilterError("**kwargs not allowed in Q()")
            kwargs[kw.arg] = _get_value(kw.value)

        # Handle positional Q arguments (for nested Q)
        q = Q(**kwargs)
        for arg in node.args:
            q = q & _ast_to_q(arg)

        return q

    # Handle binary operators (| and &). A run of one operator becomes one flat
    # Q: ``Q(a=1) | Q(b=2) | ...`` folded pairwise nests a level per term (each
    # Q(...) is an AND node, so ``|`` cannot merge them), and the ORM recurses
    # through every level when it builds the WHERE clause.
    if isinstance(node, ast.BinOp):
        if isinstance(node.op, ast.BitOr):
            connector = QOperator.OR
        elif isinstance(node.op, ast.BitAnd):
            connector = QOperator.AND
        else:
            raise QFilterError(f"Unsupported operator: {type(node.op).__name__}")
        # An empty Q() is the identity of & and |, as in Q itself.
        operands = [q for q in map(_ast_to_q, _operands(node)) if not q._is_empty()]
        if len(operands) < 2:
            return operands[0] if operands else Q()
        return Q(*operands, _connector=connector)

    # Handle unary NOT (~)
    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.Invert):
            return ~_ast_to_q(node.operand)
        else:
            raise QFilterError(f"Unsupported unary operator: {type(node.op).__name__}")

    raise QFilterError(f"Unexpected node type: {type(node).__name__}")


def _get_value(node: ast.AST) -> Any:
    """Extract a Python value from an AST node."""

    # Constant values (str, int, float, bool, None)
    if isinstance(node, ast.Constant):
        return node.value

    # Negative numbers: -3 parses as a minus applied to 3
    if isinstance(node, ast.UnaryOp) and _is_negative_number(node):
        return -node.operand.value  # type: ignore[attr-defined]

    # List values (for __in lookups)
    if isinstance(node, ast.List):
        return [_get_value(elt) for elt in node.elts]

    # Tuple values
    if isinstance(node, ast.Tuple):
        return tuple(_get_value(elt) for elt in node.elts)

    # Name references (True, False, None)
    if isinstance(node, ast.Name):
        if node.id == 'True':
            return True
        elif node.id == 'False':
            return False
        elif node.id == 'None':
            return None
        else:
            raise QFilterError(f"Disallowed name in value: {node.id}")

    raise QFilterError(f"Unsupported value type: {type(node).__name__}")
