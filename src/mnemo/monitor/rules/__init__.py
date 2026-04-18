"""Monitor rule engine — 3 domains (search_quality / knowledge_health / behavior_compliance).

Each rule is an ``async def evaluate(session, config) -> RuleResult | None`` —
returning ``None`` means "not triggered". Rules are pure readers: they query
``monitor_event`` / ``knowledge`` and evaluate threshold logic against
``RuleConfig`` knobs. Writing to ``alert_history`` / ``mon_rule_state`` and
sending notifications are runner-side concerns, not the rule's.

Design references:
- docs/phase3/MONITOR_DESIGN.md §4.4 (rule catalog)
- docs/phase3/MONITOR_AGENT_IMPL_PLAN.md §3 (thresholds + dedup keys)

Severity levels follow the design:
- ``info`` — observation, no immediate action expected
- ``warning`` — agent should investigate next cycle
- ``critical`` — must be surfaced immediately (latency / misleading burst)

``load_rules(domain)`` adapts the per-domain async ``evaluate_*`` functions
into the ``RuleProtocol`` the runner expects (id / severity / cooldown_s /
evaluate(session) -> list[Alert]). Severity + cooldown metadata come from
MONITOR_AGENT_IMPL_PLAN.md §3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

from sqlalchemy.ext.asyncio import AsyncSession

Severity = Literal["info", "warning", "critical"]


@dataclass(frozen=True)
class RuleResult:
    """A triggered rule's firing payload.

    ``details`` is a free-form dict — runner serialises it into
    ``alert_history.message`` (or a future payload_json column) so the CLI
    replay command can re-render without re-running the rule.
    """

    rule_id: str
    severity: Severity
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class RuleConfig:
    """Thresholds for all 15 rules. Defaults match MONITOR_AGENT_IMPL_PLAN.md §7.

    Kept as a plain dataclass (not pydantic) so tests can ``RuleConfig()`` or
    ``RuleConfig(mon_search_empty_streak_n=2)`` without env-variable coupling.
    Production wires this from ``MnemoConfig`` by reading the same attribute
    names — see runner.py (B4).
    """

    # search_quality
    mon_search_empty_streak_n: int = 3
    mon_search_low_top1: float = 0.5
    mon_search_low_top1_min_samples: int = 5
    mon_search_loop_window_s: int = 300
    mon_search_loop_repeat_n: int = 3
    mon_search_latency_ms: int = 500
    mon_search_latency_streak_n: int = 5
    mon_search_no_feedback_window_s: int = 86400
    mon_search_no_feedback_min_hits: int = 5

    # knowledge_health
    mon_health_stale_ratio: float = 0.4
    mon_health_no_writes_days: int = 7
    mon_health_empty_ratio_threshold: float = 0.5
    mon_health_empty_ratio_min_samples: int = 20
    mon_health_backpressure_dropped_min: int = 1
    mon_feedback_misleading_threshold: int = 3
    mon_feedback_misleading_window_days: int = 30
    mon_write_gate_supersede_ratio: float = 0.4
    mon_write_gate_supersede_min_samples: int = 5
    mon_write_evidence_weak_ratio: float = 0.3
    mon_write_evidence_weak_min_samples: int = 5

    # behavior_compliance
    mon_behavior_idle_no_search_window_s: int = 7200
    mon_behavior_idle_min_non_search_calls: int = 3
    mon_behavior_misleading_no_update_days: int = 30
    mon_behavior_misleading_no_update_threshold: int = 3
    mon_behavior_create_presearch_window_s: int = 60


EvaluateFn = Callable[[AsyncSession, "RuleConfig"], Awaitable["RuleResult | None"]]


@dataclass
class _Alert:
    """Runner-side alert shape (see runner.Alert). Kept local to avoid an
    import cycle with ``mnemo.monitor.runner`` which imports from this package.
    """

    rule_id: str
    severity: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class _RuleAdapter:
    """Wraps an ``evaluate(session, config)`` coroutine as a runner-facing Rule.

    The runner calls ``rule.evaluate(session)`` (single arg) and iterates the
    returned list. We inject a shared ``RuleConfig`` (defaults) and translate
    ``None`` / ``RuleResult`` into the runner's list-of-Alert shape.
    """

    id: str
    severity: Severity
    cooldown_s: float
    _fn: EvaluateFn
    _config: "RuleConfig"

    async def evaluate(self, session: AsyncSession) -> list[_Alert]:
        result = await self._fn(session, self._config)
        if result is None:
            return []
        return [
            _Alert(
                rule_id=result.rule_id,
                severity=result.severity,
                message=result.message,
                details=dict(result.details),
            )
        ]


# Severity + cooldown per rule — source: MONITOR_AGENT_IMPL_PLAN.md §3.
# Keyed by domain, value is a tuple of (rule_id, severity, cooldown_s,
# evaluate_fn_name) — the fn is resolved by import at load time.
_DOMAIN_SPECS: dict[str, tuple[tuple[str, Severity, float, str], ...]] = {
    "search_quality": (
        ("search.empty_streak", "warning", 600.0, "evaluate_empty_streak"),
        ("search.low_top1", "info", 3600.0, "evaluate_low_top1"),
        ("search.loop_suspect", "warning", 300.0, "evaluate_loop_suspect"),
        ("search.latency_sustained", "critical", 600.0, "evaluate_latency_sustained"),
        ("search.no_follow_up_feedback", "info", 86400.0, "evaluate_no_follow_up_feedback"),
    ),
    "knowledge_health": (
        ("health.stale_ratio", "info", 86400.0, "evaluate_stale_ratio"),
        ("health.no_writes", "info", 86400.0, "evaluate_no_writes"),
        ("health.high_empty_ratio", "warning", 3600.0, "evaluate_high_empty_ratio"),
        ("health.monitor_backpressure", "warning", 1800.0, "evaluate_monitor_backpressure"),
        ("feedback.misleading_threshold", "warning", 600.0, "evaluate_misleading_threshold"),
        ("write.gate_supersede_spike", "warning", 3600.0, "evaluate_gate_supersede_spike"),
        ("write.evidence_weak_spike", "info", 86400.0, "evaluate_evidence_weak_spike"),
    ),
    "behavior_compliance": (
        ("behavior.long_idle_no_search", "warning", 3600.0, "evaluate_long_idle_no_search"),
        ("behavior.misleading_no_update", "warning", 86400.0, "evaluate_misleading_no_update"),
        ("behavior.create_without_search", "info", 3600.0, "evaluate_create_without_search"),
    ),
}


def load_rules(domain: str, config: "RuleConfig | None" = None) -> list[_RuleAdapter]:
    """Return runner-ready Rule objects for ``domain``.

    Raises ``ValueError`` for an unknown domain — the CLI catches and logs.
    """
    specs = _DOMAIN_SPECS.get(domain)
    if specs is None:
        raise ValueError(f"unknown domain: {domain!r}")

    if domain == "search_quality":
        from mnemo.monitor.rules import search_quality as module
    elif domain == "knowledge_health":
        from mnemo.monitor.rules import knowledge_health as module
    else:
        from mnemo.monitor.rules import behavior_compliance as module

    cfg = config or RuleConfig()
    adapters: list[_RuleAdapter] = []
    for rule_id, severity, cooldown_s, fn_name in specs:
        fn = getattr(module, fn_name)
        adapters.append(
            _RuleAdapter(
                id=rule_id,
                severity=severity,
                cooldown_s=cooldown_s,
                _fn=fn,
                _config=cfg,
            )
        )
    return adapters


__all__ = ["RuleConfig", "RuleResult", "Severity", "load_rules"]
