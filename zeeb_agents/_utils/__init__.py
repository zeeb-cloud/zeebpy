"""Internal utilities for zeeb_agents."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentResult:
    """Uniform return type for all agent functions."""

    success: bool
    message: str
    data: dict[str, Any] | None = field(default=None)

    def __bool__(self) -> bool:
        return self.success
