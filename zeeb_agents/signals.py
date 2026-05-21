"""Agent functions for scaffolding and managing signal receivers in app modules."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from zeeb_agents._utils import AgentResult
from zeeb_agents._utils.project import get_app_path, require_project_root

_VALID_SIGNALS = frozenset({"pre_save", "post_save", "pre_delete", "post_delete"})

_SIGNALS_HEADER = """\
\"\"\"Signal receivers for {app} app.\"\"\"

from __future__ import annotations

from zeeb_orm.signals import {signal}, receiver

from .models import {model}

"""

_RECEIVER_TEMPLATE = """\
@receiver({signal}, sender={model})
async def {func}(sender, instance, **kwargs):
    pass
"""

_ADDITIONAL_TEMPLATE = """\

@receiver({signal}, sender={model})
async def {func}(sender, instance, **kwargs):
    pass
"""


def _signals_path(root: Path, app: str) -> Path | None:
    app_path = get_app_path(root, app)
    if app_path is None:
        return None
    return app_path / "signals.py"


def _parse_receivers(source: str) -> list[dict]:
    """Parse all @receiver(...) decorated functions from source text.

    Returns a list of dicts with keys: func_name, signal, sender, decorator_line,
    func_start, func_end (line indices, 0-based).
    """
    lines = source.splitlines()
    receivers = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("@receiver("):
            decorator_line = i
            # Collect full decorator (may span multiple lines)
            decorator = line
            j = i + 1
            while ")" not in decorator:
                decorator += " " + lines[j].strip()
                j += 1
            # Parse signal and sender from decorator
            sig_match = re.search(r"@receiver\(\s*(\w+)", decorator)
            sender_match = re.search(r"sender\s*=\s*(\w+)", decorator)
            signal_name = sig_match.group(1) if sig_match else None
            sender_name = sender_match.group(1) if sender_match else None
            # Find async def / def line
            func_line = j
            while func_line < len(lines) and not re.match(
                r"\s*(async\s+)?def\s+", lines[func_line]
            ):
                func_line += 1
            if func_line >= len(lines):
                i = j
                continue
            func_match = re.match(r"\s*(?:async\s+)?def\s+(\w+)", lines[func_line])
            func_name = func_match.group(1) if func_match else None
            # Find end of function (next unindented line or EOF)
            func_end = func_line + 1
            while func_end < len(lines):
                next_line = lines[func_end]
                if next_line.strip() == "" or next_line.strip().startswith("#"):
                    func_end += 1
                    continue
                if not next_line[0].isspace() or next_line.startswith("@"):
                    break
                func_end += 1
            receivers.append({
                "func_name": func_name,
                "signal": signal_name,
                "sender": sender_name,
                "decorator_line": decorator_line,
                "func_start": func_line,
                "func_end": func_end,
            })
            i = func_end
        else:
            i += 1
    return receivers


async def create_signal_receiver(
    app: str,
    signal_name: str,
    model_name: str,
    function_name: str,
    project_root: Path | None = None,
) -> AgentResult:
    """Create an async signal receiver stub in ``{app}/signals.py``.

    Creates ``signals.py`` if it does not exist.

    Args:
        app: App name (e.g. ``"blog"``).
        signal_name: One of ``pre_save``, ``post_save``, ``pre_delete``, ``post_delete``.
        model_name: Model class name the receiver listens on.
        function_name: Name for the new receiver function.
        project_root: Auto-detected if ``None``.
    """
    if signal_name not in _VALID_SIGNALS:
        return AgentResult(
            success=False,
            message=f"Invalid signal '{signal_name}'. Must be one of: {', '.join(sorted(_VALID_SIGNALS))}",
        )
    try:
        root = require_project_root(project_root)

        def _write() -> tuple[str, bool]:
            path = _signals_path(root, app)
            if path is None:
                raise FileNotFoundError(f"App '{app}' not found in project")
            existed = path.exists()
            if existed:
                source = path.read_text()
                # Check for duplicate function name
                if re.search(rf"\bdef\s+{re.escape(function_name)}\b", source):
                    raise ValueError(f"Receiver '{function_name}' already exists in {path.name}")
                # Append to existing file
                block = _ADDITIONAL_TEMPLATE.format(
                    signal=signal_name, model=model_name, func=function_name
                )
                path.write_text(source.rstrip() + "\n" + block)
            else:
                content = _SIGNALS_HEADER.format(
                    app=app, signal=signal_name, model=model_name
                ) + _RECEIVER_TEMPLATE.format(
                    signal=signal_name, model=model_name, func=function_name
                )
                path.write_text(content)
            return str(path.relative_to(root)), existed

        rel_path, existed = await asyncio.to_thread(_write)
        action = "Updated" if existed else "Created"
        return AgentResult(
            success=True,
            message=f"{action} {rel_path} with receiver '{function_name}'",
            data={
                "path": rel_path,
                "app": app,
                "signal": signal_name,
                "model": model_name,
                "function": function_name,
                "action": action.lower(),
            },
        )
    except (FileNotFoundError, ValueError) as exc:
        return AgentResult(success=False, message=str(exc))
    except Exception as exc:
        return AgentResult(success=False, message=str(exc))


async def list_signal_receivers(
    app: str,
    project_root: Path | None = None,
) -> AgentResult:
    """List all ``@receiver(...)`` decorated functions in ``{app}/signals.py``.

    Args:
        app: App name.
        project_root: Auto-detected if ``None``.
    """
    try:
        root = require_project_root(project_root)

        def _read() -> list[dict]:
            path = _signals_path(root, app)
            if path is None:
                raise FileNotFoundError(f"App '{app}' not found in project")
            if not path.exists():
                return []
            source = path.read_text()
            return [
                {k: v for k, v in r.items() if k not in ("decorator_line", "func_start", "func_end")}
                for r in _parse_receivers(source)
            ]

        receivers = await asyncio.to_thread(_read)
        return AgentResult(
            success=True,
            message=f"Found {len(receivers)} receiver(s) in {app}/signals.py",
            data={"app": app, "receivers": receivers, "count": len(receivers)},
        )
    except FileNotFoundError as exc:
        return AgentResult(success=False, message=str(exc))
    except Exception as exc:
        return AgentResult(success=False, message=str(exc))


async def read_signal_receiver(
    app: str,
    function_name: str,
    project_root: Path | None = None,
) -> AgentResult:
    """Return the full source (decorator + function body) of a receiver.

    Args:
        app: App name.
        function_name: Name of the receiver function.
        project_root: Auto-detected if ``None``.
    """
    try:
        root = require_project_root(project_root)

        def _read() -> dict:
            path = _signals_path(root, app)
            if path is None:
                raise FileNotFoundError(f"App '{app}' not found in project")
            if not path.exists():
                raise FileNotFoundError(f"{app}/signals.py does not exist")
            source = path.read_text()
            lines = source.splitlines()
            for r in _parse_receivers(source):
                if r["func_name"] == function_name:
                    chunk = "\n".join(lines[r["decorator_line"]:r["func_end"]])
                    return {**r, "source": chunk}
            raise ValueError(f"Receiver '{function_name}' not found in {app}/signals.py")

        result = await asyncio.to_thread(_read)
        return AgentResult(
            success=True,
            message=f"Found receiver '{function_name}' in {app}/signals.py",
            data={
                "func_name": result["func_name"],
                "signal": result["signal"],
                "sender": result["sender"],
                "source": result["source"],
            },
        )
    except (FileNotFoundError, ValueError) as exc:
        return AgentResult(success=False, message=str(exc))
    except Exception as exc:
        return AgentResult(success=False, message=str(exc))


async def edit_signal_receiver(
    app: str,
    function_name: str,
    new_body: str,
    project_root: Path | None = None,
) -> AgentResult:
    """Replace the body of an existing receiver function.

    The decorator line is preserved; only the function body is replaced.

    Args:
        app: App name.
        function_name: Name of the receiver function to edit.
        new_body: New function body (indented with 4 spaces).
        project_root: Auto-detected if ``None``.
    """
    try:
        root = require_project_root(project_root)

        def _edit() -> str:
            path = _signals_path(root, app)
            if path is None:
                raise FileNotFoundError(f"App '{app}' not found in project")
            if not path.exists():
                raise FileNotFoundError(f"{app}/signals.py does not exist")
            source = path.read_text()
            lines = source.splitlines(keepends=True)
            for r in _parse_receivers(source):
                if r["func_name"] != function_name:
                    continue
                # Preserve decorator + def line(s), replace body
                func_def_line = r["func_start"]
                # Ensure new_body is properly indented
                body_lines = []
                for line in new_body.splitlines():
                    stripped = line.lstrip()
                    body_lines.append(f"    {stripped}\n" if stripped else "    pass\n")
                if not body_lines:
                    body_lines = ["    pass\n"]
                new_lines = (
                    lines[: func_def_line + 1]  # up to and including def line
                    + body_lines
                    + (lines[r["func_end"]:] if r["func_end"] < len(lines) else [])
                )
                path.write_text("".join(new_lines))
                return str(path.relative_to(root))
            raise ValueError(f"Receiver '{function_name}' not found in {app}/signals.py")

        rel_path = await asyncio.to_thread(_edit)
        return AgentResult(
            success=True,
            message=f"Updated body of '{function_name}' in {rel_path}",
            data={"path": rel_path, "func_name": function_name},
        )
    except (FileNotFoundError, ValueError) as exc:
        return AgentResult(success=False, message=str(exc))
    except Exception as exc:
        return AgentResult(success=False, message=str(exc))


async def delete_signal_receiver(
    app: str,
    function_name: str,
    project_root: Path | None = None,
) -> AgentResult:
    """Remove a receiver function and its ``@receiver(...)`` decorator.

    Args:
        app: App name.
        function_name: Name of the receiver function to remove.
        project_root: Auto-detected if ``None``.
    """
    try:
        root = require_project_root(project_root)

        def _delete() -> str:
            path = _signals_path(root, app)
            if path is None:
                raise FileNotFoundError(f"App '{app}' not found in project")
            if not path.exists():
                raise FileNotFoundError(f"{app}/signals.py does not exist")
            source = path.read_text()
            lines = source.splitlines(keepends=True)
            for r in _parse_receivers(source):
                if r["func_name"] != function_name:
                    continue
                new_lines = lines[: r["decorator_line"]] + lines[r["func_end"]:]
                path.write_text("".join(new_lines))
                return str(path.relative_to(root))
            raise ValueError(f"Receiver '{function_name}' not found in {app}/signals.py")

        rel_path = await asyncio.to_thread(_delete)
        return AgentResult(
            success=True,
            message=f"Deleted receiver '{function_name}' from {rel_path}",
            data={"path": rel_path, "func_name": function_name},
        )
    except (FileNotFoundError, ValueError) as exc:
        return AgentResult(success=False, message=str(exc))
    except Exception as exc:
        return AgentResult(success=False, message=str(exc))


async def list_model_signals(
    app: str,
    model_name: str,
    project_root: Path | None = None,
) -> AgentResult:
    """Return all receivers registered for a specific model.

    Filters by ``sender=<model_name>`` in ``{app}/signals.py``.

    Args:
        app: App name.
        model_name: Model class name to filter by.
        project_root: Auto-detected if ``None``.
    """
    try:
        root = require_project_root(project_root)

        def _read() -> list[dict]:
            path = _signals_path(root, app)
            if path is None:
                raise FileNotFoundError(f"App '{app}' not found in project")
            if not path.exists():
                return []
            source = path.read_text()
            return [
                {"func_name": r["func_name"], "signal": r["signal"]}
                for r in _parse_receivers(source)
                if r["sender"] == model_name
            ]

        receivers = await asyncio.to_thread(_read)
        return AgentResult(
            success=True,
            message=f"Found {len(receivers)} receiver(s) for model '{model_name}' in {app}/signals.py",
            data={"app": app, "model": model_name, "receivers": receivers, "count": len(receivers)},
        )
    except FileNotFoundError as exc:
        return AgentResult(success=False, message=str(exc))
    except Exception as exc:
        return AgentResult(success=False, message=str(exc))
