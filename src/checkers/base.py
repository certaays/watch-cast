from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class CheckResult:
    """Result returned by a checker run."""

    should_notify: bool
    message: str = ""
    new_state: dict[str, Any] | None = None


class BaseChecker(ABC):
    """Base class for all notification checkers."""

    @abstractmethod
    def check(self, params: dict[str, Any], state: dict[str, Any]) -> CheckResult:
        """Run the check and return whether a notification should be sent."""
        raise NotImplementedError

