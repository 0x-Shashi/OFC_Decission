"""Multi-strategy element resolution with fallback-based self-healing. See docs/PLAN.md §8."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from quartermaster.domain.models import Locator, LocatorStrategy


class ElementCounter(Protocol):
    def count(self, strategy: LocatorStrategy, value: str) -> int: ...


@dataclass
class Resolution:
    strategy: LocatorStrategy
    value: str
    index: int  # position of the winning strategy in the locator; 0 = primary
    attempts: list[str] = field(default_factory=list)

    @property
    def healed(self) -> bool:
        return self.index > 0

    @property
    def confidence(self) -> float:
        """Heuristic confidence that the fallback hit the intended element.

        Later fallbacks (css/xpath) are less trustworthy than earlier user-facing ones.
        """
        return max(0.5, 0.95 - 0.1 * (self.index - 1)) if self.healed else 1.0


class ResolutionError(LookupError):
    def __init__(self, locator: Locator, attempts: list[str]):
        self.locator = locator
        self.attempts = attempts
        super().__init__(
            f"could not resolve '{locator.description or locator.ordered()[0][1]}': " + "; ".join(attempts)
        )


def resolve(locator: Locator, page: ElementCounter) -> Resolution:
    """Return the first strategy that matches exactly one element.

    Zero matches and ambiguous (>1) matches are both skipped: clicking the wrong one of two
    'Submit' buttons is worse than failing loudly.
    """
    attempts: list[str] = []
    for i, (strategy, value) in enumerate(locator.ordered()):
        n = page.count(strategy, value)
        if n == 1:
            return Resolution(strategy=strategy, value=value, index=i, attempts=attempts)
        attempts.append(f"{strategy.value}={value!r} matched {n}")
    raise ResolutionError(locator, attempts)
