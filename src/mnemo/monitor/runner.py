"""Monitor runner — main poll loop for a single domain.

Responsibilities:
    * Poll every ``polling_s`` (default 10s).
    * For each rule in the domain, call ``rule.evaluate(session) -> list[Alert]``.
    * For each alert, dispatch to ``Notifier.send_alert`` and persist
      ``AlertHistory`` + update ``MonRuleState``.
    * Catch per-tick exceptions so a single bad rule never kills the loop.
    * Respect SIGTERM/SIGINT via a shared ``asyncio.Event``.

Rule interface (duck-typed; rules/ is owned by a peer, we guard import):
    class Rule:
        id: str
        severity: str              # info / warning / critical
        cooldown_s: float
        async def evaluate(session) -> list[Alert]: ...

Alert shape (dataclass or duck-typed):
    rule_id: str
    severity: str
    message: str
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mnemo.monitor.models import AlertHistory, MonRuleState
from mnemo.monitor.notifier import Notifier

logger = logging.getLogger(__name__)

DEFAULT_POLLING_S = 10.0


class RuleProtocol(Protocol):
    id: str
    severity: str
    cooldown_s: float

    async def evaluate(self, session: AsyncSession) -> list[Any]: ...


@dataclass
class Alert:
    rule_id: str
    severity: str
    message: str


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MonitorRunner:
    """Runs a single domain's rule set in a poll loop.

    Multi-domain orchestration happens at the CLI layer via ``asyncio.gather``
    over multiple runners — each runner owns one domain.
    """

    def __init__(
        self,
        domain: str,
        rules: Iterable[RuleProtocol],
        session_factory: async_sessionmaker[AsyncSession],
        notifier: Notifier | None = None,
        polling_s: float = DEFAULT_POLLING_S,
        shutdown: asyncio.Event | None = None,
    ) -> None:
        self.domain = domain
        self.rules = list(rules)
        self._session_factory = session_factory
        self._notifier = notifier or Notifier()
        self._polling_s = polling_s
        self._shutdown = shutdown or asyncio.Event()

    @property
    def shutdown_event(self) -> asyncio.Event:
        return self._shutdown

    def request_shutdown(self) -> None:
        self._shutdown.set()

    async def run(self) -> None:
        logger.info(
            "monitor runner up domain=%s rules=%d polling=%.1fs",
            self.domain,
            len(self.rules),
            self._polling_s,
        )
        try:
            while not self._shutdown.is_set():
                tick_started = time.monotonic()
                try:
                    await self._run_tick()
                except Exception:  # single-tick failure must not kill the loop
                    logger.exception(
                        "monitor tick crashed domain=%s", self.domain
                    )
                elapsed = time.monotonic() - tick_started
                sleep_s = max(1.0, self._polling_s - elapsed)
                try:
                    await asyncio.wait_for(self._shutdown.wait(), timeout=sleep_s)
                except asyncio.TimeoutError:
                    pass
        finally:
            logger.info("monitor runner down domain=%s", self.domain)

    async def _run_tick(self) -> None:
        if not self.rules:
            return
        async with self._session_factory() as session:
            for rule in self.rules:
                try:
                    await self._evaluate_rule(session, rule)
                except Exception:
                    logger.exception(
                        "rule evaluate failed domain=%s rule=%s",
                        self.domain,
                        getattr(rule, "id", "<unknown>"),
                    )

    async def _evaluate_rule(
        self, session: AsyncSession, rule: RuleProtocol
    ) -> None:
        rule_id = rule.id
        severity = rule.severity
        cooldown_s = float(getattr(rule, "cooldown_s", 0) or 0)

        if severity != "critical" and await self._in_cooldown(
            session, rule_id, cooldown_s
        ):
            return

        alerts = await rule.evaluate(session)
        if not alerts:
            return

        for alert in alerts:
            msg = getattr(alert, "message", str(alert))
            # rule.severity is authoritative; alert may override (e.g. escalate).
            sev = getattr(alert, "severity", severity)
            dispatched = self._notifier.send_alert(
                rule_id=rule_id, severity=sev, message=msg
            )
            if dispatched:
                await self._record_alert(session, rule_id, sev, msg, cooldown_s)
                await self._update_rule_state(session, rule_id, self.domain)
        await session.commit()

    async def _in_cooldown(
        self, session: AsyncSession, rule_id: str, cooldown_s: float
    ) -> bool:
        if cooldown_s <= 0:
            return False
        cutoff = _utcnow() - timedelta(seconds=cooldown_s)
        stmt = (
            select(AlertHistory.id)
            .where(AlertHistory.rule_id == rule_id)
            .where(AlertHistory.notified_at >= cutoff)
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none() is not None

    @staticmethod
    async def _record_alert(
        session: AsyncSession,
        rule_id: str,
        severity: str,
        message: str,
        cooldown_s: float,
    ) -> None:
        now = _utcnow()
        entry = AlertHistory(
            rule_id=rule_id,
            severity=severity,
            message=message,
            notified_at=now,
            cooldown_until=(
                now + timedelta(seconds=cooldown_s) if cooldown_s > 0 else None
            ),
        )
        session.add(entry)

    @staticmethod
    async def _update_rule_state(
        session: AsyncSession, rule_id: str, domain: str
    ) -> None:
        now = _utcnow()
        existing = await session.get(MonRuleState, rule_id)
        if existing is None:
            session.add(
                MonRuleState(
                    rule_id=rule_id,
                    domain=domain,
                    last_triggered_at=now,
                    trigger_count=1,
                )
            )
        else:
            existing.last_triggered_at = now
            existing.trigger_count = (existing.trigger_count or 0) + 1
            existing.updated_at = now


async def run_all_domains(
    runners: list[MonitorRunner],
    shutdown: asyncio.Event,
) -> None:
    """Run multiple domain runners concurrently. Returns when shutdown fires."""
    # Wire the shared shutdown event so SIGTERM fans out to every runner.
    for runner in runners:
        runner._shutdown = shutdown
    await asyncio.gather(*(r.run() for r in runners), return_exceptions=False)


__all__ = [
    "Alert",
    "MonitorRunner",
    "RuleProtocol",
    "run_all_domains",
    "DEFAULT_POLLING_S",
]
