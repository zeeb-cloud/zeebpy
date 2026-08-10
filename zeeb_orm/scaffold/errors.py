"""The scaffolding layer's expected-failure exception."""

from __future__ import annotations


class ScaffoldError(Exception):
    """A targeted scaffolding failure carrying a machine-readable code.

    ``zeeb_orm`` sits below ``zeeb_agents`` in the package layering and so
    cannot raise ``AgentError``. The adapter in ``zeeb_agents._utils.wiring``
    re-raises this as an ``AgentError`` with the identical ``code`` and payload,
    which keeps the agent-facing error contract (``file_not_found``,
    ``invalid_input``, ``setting_not_found``) unchanged.
    """

    def __init__(self, message: str, *, code: str, **data) -> None:
        super().__init__(message)
        self.code = code
        self.data = {key: value for key, value in data.items() if value is not None}
