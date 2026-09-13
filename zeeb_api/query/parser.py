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
- Values must be safe literals
"""

from __future__ import annotations

import ast
from typing import Any

from zeeb_orm.query.q import Q


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
}

# Allowed constant types
ALLOWED_CONSTANT_TYPES = (str, int, float, bool, type(None))


def parse_q_filter(expr: str) -> Q:
    """
    Safely parse a Q filter expression string into a Q object.
    
    Args:
        expr: Q filter expression string, e.g. "Q(name='john') | Q(active=True)"
    
    Returns:
        Q object that can be used with QuerySet.filter()
    
    Raises:
        QFilterError: If expression is invalid or contains disallowed constructs
    
    Examples:
        >>> parse_q_filter("Q(name__icontains='ring')")
        Q(name__icontains='ring')
        
        >>> parse_q_filter("Q(price__gte=10) & Q(price__lte=50)")
        Q(price__gte=10) & Q(price__lte=50)
        
        >>> parse_q_filter("~Q(deleted=True)")
        ~Q(deleted=True)
    """
    if not expr or not expr.strip():
        return Q()
    
    expr = expr.strip()
    
    try:
        tree = ast.parse(expr, mode='eval')
    except SyntaxError as e:
        raise QFilterError(f"Invalid syntax: {e}")
    
    # Validate all nodes are allowed
    _validate_ast(tree)
    
    # Convert AST to Q object
    return _ast_to_q(tree.body)


def extract_q_fields(expr: str) -> set[str]:
    """Return the root field names referenced by a Q filter expression.

    For each ``Q(field__lookup=value)`` keyword the root before the first
    ``__`` is collected, so callers can validate the expression against an
    allow-list of permitted fields (preventing filtering on columns the API
    does not expose, e.g. a password hash).
    """
    if not expr or not expr.strip():
        return set()

    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as e:
        raise QFilterError(f"Invalid syntax: {e}")

    fields: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Q"
        ):
            for kw in node.keywords:
                if kw.arg:
                    fields.add(kw.arg.split("__", 1)[0])
    return fields


def _validate_ast(node: ast.AST) -> None:
    """Recursively validate that all AST nodes are allowed."""
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
    
    # Check Call nodes - only allow Q()
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id != 'Q':
            raise QFilterError("Only Q() calls are allowed")
        # Q() should not have positional args (except other Q objects via combinators)
        for arg in node.args:
            if not isinstance(arg, (ast.Call, ast.BinOp, ast.UnaryOp)):
                raise QFilterError("Q() positional arguments must be other Q expressions")
    
    # Recursively validate children
    for child in ast.iter_child_nodes(node):
        _validate_ast(child)


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
    
    # Handle binary operators (| and &)
    if isinstance(node, ast.BinOp):
        left = _ast_to_q(node.left)
        right = _ast_to_q(node.right)
        
        if isinstance(node.op, ast.BitOr):
            return left | right
        elif isinstance(node.op, ast.BitAnd):
            return left & right
        else:
            raise QFilterError(f"Unsupported operator: {type(node.op).__name__}")
    
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
