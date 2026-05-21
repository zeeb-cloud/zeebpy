"""Agent functions for deployment scaffolding and production readiness checks."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from zeeb_agents._utils import AgentResult
from zeeb_agents._utils.project import load_project_settings, require_project_root

_DOCKERFILE_TEMPLATE = """\
# syntax=docker/dockerfile:1

FROM python:{python_version}-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \\
    PYTHONUNBUFFERED=1 \\
    PIP_NO_CACHE_DIR=1

WORKDIR /app

FROM base AS builder

RUN pip install --upgrade pip
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM base AS runner

COPY --from=builder /usr/local/lib/python{python_version}/site-packages /usr/local/lib/python{python_version}/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY . .

EXPOSE {port}

CMD ["python", "manage.py", "runserver", "0.0.0.0:{port}"]
"""

_DOCKERIGNORE = """\
__pycache__
*.pyc
*.pyo
.git
.env
.env.*
*.sqlite3
*.db
.venv
venv
node_modules
"""


async def generate_dockerfile(
    python_version: str = "3.12",
    port: int = 8000,
    project_root: Path | None = None,
) -> AgentResult:
    """Generate a production-ready ``Dockerfile`` in the project root.

    Uses a multi-stage build (``builder`` + ``runner``) to keep the final
    image lean.  Also writes a ``.dockerignore`` if one does not exist.

    Args:
        python_version: Python version tag to base the image on.
            Defaults to ``"3.12"``.
        port: Port the application listens on.  Defaults to ``8000``.
        project_root: Auto-detected if ``None``.

    Example::

        await generate_dockerfile(python_version="3.12", port=8080)
    """
    try:
        root = require_project_root(project_root)
        dockerfile = root / "Dockerfile"
        dockerignore = root / ".dockerignore"

        if dockerfile.exists():
            return AgentResult(
                success=False,
                message="Dockerfile already exists.  Delete it first to regenerate.",
                data={"path": "Dockerfile"},
            )

        def _write() -> list[str]:
            written = []
            content = _DOCKERFILE_TEMPLATE.format(
                python_version=python_version,
                port=port,
            )
            dockerfile.write_text(content, encoding="utf-8")
            written.append("Dockerfile")
            if not dockerignore.exists():
                dockerignore.write_text(_DOCKERIGNORE, encoding="utf-8")
                written.append(".dockerignore")
            return written

        written = await asyncio.to_thread(_write)
        return AgentResult(
            success=True,
            message=f"Dockerfile generated (Python {python_version}, port {port}).",
            data={
                "files_written": written,
                "python_version": python_version,
                "port": port,
            },
        )
    except Exception as exc:
        return AgentResult(success=False, message=str(exc))


async def generate_requirements(
    output_path: str = "requirements.txt",
    project_root: Path | None = None,
) -> AgentResult:
    """Generate a ``requirements.txt`` from ``pip freeze`` output.

    Runs ``pip freeze`` in a subprocess and writes the result to *output_path*.
    The file is filtered to remove editable installs (``-e .``) and local
    ``file://`` references.

    Args:
        output_path: Output file name/path, relative to project root.
            Defaults to ``"requirements.txt"``.
        project_root: Auto-detected if ``None``.
    """
    try:
        root = require_project_root(project_root)

        proc = await asyncio.create_subprocess_exec(
            "pip", "freeze",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return AgentResult(
                success=False,
                message=f"pip freeze failed: {stderr.decode().strip()}",
            )

        lines = [
            line for line in stdout.decode().splitlines()
            if line and not line.startswith(("-e ", "file://"))
        ]
        content = "\n".join(lines) + "\n"

        out = root / output_path
        await asyncio.to_thread(out.write_text, content, "utf-8")
        return AgentResult(
            success=True,
            message=f"requirements.txt written ({len(lines)} package(s)).",
            data={"path": output_path, "package_count": len(lines)},
        )
    except Exception as exc:
        return AgentResult(success=False, message=str(exc))


async def check_production_readiness(
    project_root: Path | None = None,
) -> AgentResult:
    """Validate that the project is ready for production deployment.

    Checks:

    - ``DEBUG`` must be ``False`` (or unset).
    - ``SECRET_KEY`` must be set and not equal to a placeholder value.
    - Database is not SQLite (SQLite is development-only).
    - A ``requirements.txt`` exists.
    - A ``Dockerfile`` exists.

    Args:
        project_root: Auto-detected if ``None``.

    Returns:
        ``AgentResult`` with ``issues`` (list of problem strings) and
        ``passed`` (list of passing checks).
    """
    try:
        root = require_project_root(project_root)

        def _check() -> dict[str, Any]:
            settings = load_project_settings(root)
            issues: list[str] = []
            passed: list[str] = []

            # DEBUG check
            debug = settings.get("DEBUG", True)
            if debug:
                issues.append("DEBUG is True — set DEBUG = False for production.")
            else:
                passed.append("DEBUG is False.")

            # SECRET_KEY check
            secret = settings.get("SECRET_KEY", "")
            placeholders = {"", "change-me", "your-secret-key", "insecure-key", "dev-secret"}
            if not secret:
                issues.append("SECRET_KEY is not set.")
            elif any(p in secret.lower() for p in placeholders):
                issues.append("SECRET_KEY looks like a placeholder — use a strong random value.")
            else:
                passed.append("SECRET_KEY is set.")

            # Database check
            db_url = settings.get("DATABASE", {}).get("url", "")
            if "sqlite" in db_url:
                issues.append("Database is SQLite — use PostgreSQL or MySQL for production.")
            elif db_url:
                passed.append(f"Database driver: {db_url.split('://')[0]}.")
            else:
                issues.append("DATABASE URL is not set.")

            # requirements.txt check
            if (root / "requirements.txt").exists():
                passed.append("requirements.txt exists.")
            else:
                issues.append("requirements.txt is missing — run generate_requirements().")

            # Dockerfile check
            if (root / "Dockerfile").exists():
                passed.append("Dockerfile exists.")
            else:
                issues.append("Dockerfile is missing — run generate_dockerfile().")

            ready = len(issues) == 0
            return {"ready": ready, "issues": issues, "passed": passed}

        result = await asyncio.to_thread(_check)
        ready = result["ready"]
        issue_count = len(result["issues"])
        return AgentResult(
            success=ready,
            message=(
                "Project is production-ready."
                if ready
                else f"Project has {issue_count} production issue(s)."
            ),
            data=result,
        )
    except Exception as exc:
        return AgentResult(success=False, message=str(exc))
