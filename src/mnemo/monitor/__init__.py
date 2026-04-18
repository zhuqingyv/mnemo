"""Monitoring subsystem — collector + storage + detectors.

- Task #51: collection layer (collector.py — ``monitor_tool`` decorator)
- Task #55: storage layer (models / retention / queries / CLI stats)
- Task #52: detection layer (rules / notifier / runner)

See docs/phase3/MONITOR_DESIGN.md.
"""

from mnemo.monitor.models import (
    AlertHistory,
    MonitorEvent,
    MonitorHealth,
    MonRuleState,
)

__all__ = [
    "AlertHistory",
    "MonitorEvent",
    "MonitorHealth",
    "MonRuleState",
]

try:  # collector lands in task #51 — import when available
    from mnemo.monitor.collector import monitor_tool, record_payload  # noqa: F401

    __all__.extend(["monitor_tool", "record_payload"])
except ImportError:  # pragma: no cover - collector not yet shipped
    pass
