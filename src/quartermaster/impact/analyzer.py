"""Release impact analysis and regression planning.

Deterministic and explainable by design: every score carries the reasons that produced it,
so a release manager can see *why* a test was selected. See docs/PLAN.md §7.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from typing import Protocol

from quartermaster.domain.models import PRIORITY_WEIGHT, ChangeType, Feature, Priority, Release, TestCase

_BASE_SEVERITY = {
    ChangeType.UI: 0.6,
    ChangeType.REPORT: 0.6,
    ChangeType.API: 0.7,
    ChangeType.PROCESS: 0.8,
    ChangeType.BOTH: 1.0,
}

_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "for",
        "from",
        "in",
        "into",
        "is",
        "it",
        "new",
        "now",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
        "you",
        "your",
        "using",
        "use",
        "when",
        "this",
        "that",
        "these",
        "those",
        "will",
        "via",
    ]
)
_WORD = re.compile(r"[a-z0-9]+")

# Below this, a feature/test pairing is treated as noise and not reported.
MATCH_THRESHOLD = 0.15
# At or above this, the test is considered to *cover* the feature. Weaker matches still add
# risk, but a feature with only weak matches is reported as uncovered.
COVERAGE_THRESHOLD = 0.5
# Tags and vocabulary shared across modules are weak evidence: "sales order" and "purchase
# order" share a word, and "redwood"/"approval" tags appear in every pillar. Matches from a
# different module are scaled down so they add some risk but never count as coverage.
CROSS_MODULE_DAMPING = 0.5


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS and len(w) > 2}


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def severity(feature: Feature, enabled_opt_ins: AbstractSet[str] = frozenset()) -> float:
    s = _BASE_SEVERITY[feature.change_type]
    if feature.customer_action_required:
        s += 0.2
    if feature.opt_in and feature.id in enabled_opt_ins:
        s += 0.1
    return min(s, 1.0)


class _Intent(Protocol):
    @property
    def intent(self) -> str: ...


class Matchable(Protocol):
    """What matching reads from a test: an automated TestCase, or a manual scenario."""

    @property
    def module(self) -> str: ...
    @property
    def product(self) -> str: ...
    @property
    def title(self) -> str: ...
    @property
    def process(self) -> str: ...
    @property
    def tags(self) -> Sequence[str]: ...
    @property
    def steps(self) -> Sequence[_Intent]: ...


def match(feature: Feature, test: Matchable) -> tuple[float, list[str]]:
    """How strongly a feature relates to a test, in [0, 1], with reasons."""
    reasons: list[str] = []
    scores: list[float] = [0.0]
    same_module = feature.module.lower() == test.module.lower()

    if feature.product.lower() == test.product.lower():
        scores.append(0.7)
        reasons.append(f"same product ({test.product})")
    elif same_module:
        scores.append(0.3)
        reasons.append(f"same module ({test.module})")

    ftags = {t.lower() for t in feature.tags}
    ttags = {t.lower() for t in test.tags}
    tag_score = _jaccard(ftags, ttags)
    if tag_score:
        # Shared tags are a deliberate signal from whoever curated them, so weight them up.
        scores.append(min(1.0, 0.4 + tag_score))
        reasons.append(f"shared tags: {', '.join(sorted(ftags & ttags))}")

    ftext = _tokens(f"{feature.title} {feature.description}")
    ttext = _tokens(" ".join([test.title, test.process, *(s.intent for s in test.steps)]))
    common = ftext & ttext
    if common:
        kw = min(1.0, len(common) / max(3, min(len(ftext), len(ttext))))
        scores.append(kw)
        reasons.append(f"keyword overlap: {', '.join(sorted(common)[:6])}")

    # Same product *and* overlapping vocabulary is stronger evidence than either alone.
    best = max(scores)
    if len(scores) > 2:
        best = min(1.0, best + 0.1 * (len(scores) - 2))
    if best and not same_module:
        best *= CROSS_MODULE_DAMPING
        reasons.append(f"different module ({feature.module} vs {test.module}): weak evidence")
    return best, reasons


@dataclass
class TestImpact:
    test: TestCase
    risk: float
    priority: float
    reasons: list[str] = field(default_factory=list)
    features: list[str] = field(default_factory=list)  # features this test covers


def analyze(
    release: Release, tests: list[TestCase], enabled_opt_ins: AbstractSet[str] = frozenset()
) -> list[TestImpact]:
    """Score every test against the release. Returns impacts sorted by priority, highest first."""
    impacts: list[TestImpact] = []
    for t in tests:
        survive = 1.0  # noisy-OR: probability the test is unaffected by every feature
        reasons: list[str] = []
        feats: list[str] = []
        for f in release.features:
            m, why = match(f, t)
            if m < MATCH_THRESHOLD:
                continue
            p = m * severity(f, enabled_opt_ins)
            survive *= 1 - p
            if m >= COVERAGE_THRESHOLD:
                feats.append(f.id)
            reasons.append(f"{f.id} '{f.title}' (p={p:.2f}): {'; '.join(why)}")
        risk = 1 - survive
        impacts.append(
            TestImpact(
                test=t,
                risk=round(risk, 4),
                priority=round(risk * PRIORITY_WEIGHT[t.priority], 4),
                reasons=reasons,
                features=feats,
            )
        )
    impacts.sort(key=lambda i: (-i.priority, -i.risk, i.test.id))
    return impacts


@dataclass
class RegressionPlan:
    selected: list[TestImpact]
    deferred: list[TestImpact]
    budget_minutes: float | None

    @property
    def total_minutes(self) -> float:
        return sum(i.test.estimated_minutes for i in self.selected)

    def uncovered_features(self, release: Release) -> list[Feature]:
        covered = {f for i in self.selected for f in i.features}
        return [f for f in release.features if f.id not in covered]


def plan(impacts: list[TestImpact], budget_minutes: float | None = None, min_risk: float = 0.05) -> RegressionPlan:
    """Pick tests by priority under a time budget.

    Critical tests are always included (even over budget) because skipping them silently is
    worse than an overrun. Tests below `min_risk` are deferred unless critical.
    """
    selected: list[TestImpact] = []
    deferred: list[TestImpact] = []
    used = 0.0

    for i in impacts:
        if i.test.priority is Priority.CRITICAL:
            selected.append(i)
            used += i.test.estimated_minutes
    for i in impacts:
        if i.test.priority is Priority.CRITICAL:
            continue
        fits = budget_minutes is None or used + i.test.estimated_minutes <= budget_minutes
        if i.risk >= min_risk and fits:
            selected.append(i)
            used += i.test.estimated_minutes
        else:
            deferred.append(i)

    selected.sort(key=lambda i: (-i.priority, i.test.id))
    return RegressionPlan(selected=selected, deferred=deferred, budget_minutes=budget_minutes)
