from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EngineState:
    last_sync_at: str | None = None
    last_cycle_at: str | None = None
    last_reconcile_at: str | None = None
    last_audit_at: str | None = None
    last_error: str | None = None
    auth_ok: bool = False
    last_run_metrics: dict = field(default_factory=dict)
