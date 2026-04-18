"""Alert notification — macOS osascript + logger dual-write.

Per-rule cooldown:
    * ``info`` / ``warning`` respect ``cooldown_s`` (default 300s).
    * ``critical`` bypasses cooldown — page every time.

The notifier is stateless across process restarts only in the in-memory sense
— the runner persists ``AlertHistory`` so cooldown survives restart via the
runner layer. In-process cooldown here is a second line of defense against
stampedes within a single tick.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

Severity = Literal["info", "warning", "critical"]

DEFAULT_COOLDOWN_S = 300.0


@dataclass
class Notifier:
    """Send alerts via macOS osascript + logger, with per-rule cooldown."""

    cooldown_s: float = DEFAULT_COOLDOWN_S
    _last_sent: dict[str, float] = field(default_factory=dict)

    def send_alert(
        self,
        rule_id: str,
        severity: Severity,
        message: str,
        *,
        title: str | None = None,
    ) -> bool:
        """Send an alert. Returns True if dispatched, False if suppressed by cooldown.

        ``critical`` always dispatches (bypasses cooldown).
        """
        now = time.monotonic()
        if severity != "critical":
            last = self._last_sent.get(rule_id)
            if last is not None and (now - last) < self.cooldown_s:
                logger.debug(
                    "alert suppressed by cooldown rule=%s remaining=%.1fs",
                    rule_id,
                    self.cooldown_s - (now - last),
                )
                return False

        self._last_sent[rule_id] = now
        title = title or f"mnemo/{rule_id}"
        self._log(severity, rule_id, message)
        self._notify_system(title, message, severity)
        return True

    @staticmethod
    def _log(severity: Severity, rule_id: str, message: str) -> None:
        level = {
            "info": logging.INFO,
            "warning": logging.WARNING,
            "critical": logging.ERROR,
        }.get(severity, logging.INFO)
        logger.log(level, "[alert %s] %s: %s", severity, rule_id, message)

    @staticmethod
    def _notify_system(title: str, message: str, severity: Severity) -> None:
        if sys.platform == "darwin":
            Notifier._notify_macos(title, message)
        elif sys.platform.startswith("linux"):
            Notifier._notify_linux(title, message)
        else:
            logger.debug("system notification unsupported on platform=%s", sys.platform)

    @staticmethod
    def _notify_macos(title: str, message: str) -> None:
        # osascript is bundled on every macOS — escape quotes to avoid injection
        # (title/message come from rule code, not user input, but defense-in-depth).
        safe_title = title.replace('"', '\\"')
        safe_message = message.replace('"', '\\"')
        script = f'display notification "{safe_message}" with title "{safe_title}"'
        try:
            subprocess.run(
                ["osascript", "-e", script],
                timeout=3,
                check=False,
                capture_output=True,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            logger.warning("osascript notify failed: %s", exc)

    @staticmethod
    def _notify_linux(title: str, message: str) -> None:
        if shutil.which("notify-send") is None:
            logger.debug("notify-send not installed, skip system notify")
            return
        try:
            subprocess.run(
                ["notify-send", title, message],
                timeout=3,
                check=False,
                capture_output=True,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.warning("notify-send failed: %s", exc)


__all__ = ["Notifier", "Severity", "DEFAULT_COOLDOWN_S"]
