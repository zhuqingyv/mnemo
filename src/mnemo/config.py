"""Configuration for mnemo."""

import os
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Scope(str, Enum):
    GLOBAL = "global"
    PROJECT = "project"
    SESSION = "session"


DATABASE_NAME = "mnemo.db"
DATA_DIR_NAME = ".mnemo"


def resolve_data_dir() -> Path:
    if config_dir := os.getenv("MNEMO_DATA_DIR"):
        return Path(config_dir)
    return Path.home() / DATA_DIR_NAME


class MnemoConfig(BaseSettings):
    data_dir: Optional[str] = Field(default=None, description="Override data directory path")
    log_level: str = Field(default="INFO")
    default_scope: Scope = Field(default=Scope.GLOBAL)
    project_name: Optional[str] = Field(default=None, description="Current project name for scoping")

    embedding_model: str = Field(default="qwen3-embedding:0.6b")
    embedding_dim: int = Field(default=1024)
    ollama_url: str = Field(default="http://localhost:11434")
    embedding_timeout_ms: int = Field(default=800)
    embedding_circuit_threshold: int = Field(default=3)
    embedding_circuit_cooldown_s: int = Field(default=60)
    embedding_warmup_timeout_ms: int = Field(default=10000)
    embedding_content_max_chars: int = Field(default=1500)

    # M3b authority rerank on top of RRF (TECH_PLAN §4 / §5). Values here are
    # the M3b-validated weights (acc 79.6% / int 89.5% / top3 90.0% / neg
    # 9/10 on real service.search). M4 task #3 attempted a 1875-combo grid
    # search but its cache path diverged from production in four places
    # (vec0 knn_limit, RRF rank depth, hard-coded cos_hit=0.8, hard-coded
    # cos_miss=0.55) so its "winner" did not reproduce on the real path
    # (dropped to acc 61.3%). Rollback was verified via
    # scripts/m4_real_eval.py across four candidate weight sets — none
    # strictly beat M3b. See docs/phase2/GROWTH_LOG.md M4 section.
    authority_multiplier: float = Field(default=0.1)
    # Absolute gate for the pure-vector path (FTS miss). When every surviving
    # candidate is vec_only and the top ``final`` score is below this value,
    # search returns an empty list. M3b raised negative-case pass from M2
    # 5/10 to 9/10; the last gap (REL-N-06 "kubernetes 集群") is a structural
    # embedding-expressiveness limit, not a rerank-tunable issue, and is
    # accepted as a known limitation.
    vec_only_min_final: float = Field(default=0.0170)
    # Multiplier applied when an unscoped query hits a project-scoped row —
    # the cross-project-contamination signal introduced in M3b §5 decision B.
    # M4 real-path eval (scripts/m4_real_eval.py) showed that lowering
    # scope_penalty below 0.8 drops INT from 17/19 to 14/19 without
    # compensating elsewhere, so the M3b value is kept.
    scope_mismatch_penalty: float = Field(default=0.8)

    # Phase 3 feature flags (TECH_PLAN §2.5 / §9). Defaults mirror
    # tests/conftest.py PHASE3_FLAG_DEFAULTS — keep both in sync.
    write_gate_enabled: bool = Field(default=True)
    freshness_enabled: bool = Field(default=True)
    state_machine_enabled: bool = Field(default=True)
    feedback_loop_enabled: bool = Field(default=True)
    contradiction_pair_enabled: bool = Field(default=True)
    context_aware_rank_enabled: bool = Field(default=False)

    # Phase 5 task-tracking (docs/phase5/TASK_TRACKING_DESIGN.md). When off,
    # task_id is neither emitted in hints nor recorded as events — the
    # dispatcher/hint behavior reverts to pre-phase5.
    task_tracking_enabled: bool = Field(default=True)

    # When FTS5 returns zero hits, progressively trim tokens from the right
    # (N, N-1, N-2, ... down to 2) and retry with the shorter query.  This
    # softens the strict AND semantics so one cold token doesn't kill the
    # whole query.  When even 2-token FTS still returns nothing, the system
    # falls through to the vec_only path (gated by ``vec_only_min_final``).
    search_progressive_trim_enabled: bool = Field(default=True)

    # Auto-link by vector similarity — on create_knowledge, top-K nearest
    # neighbors above ``auto_link_threshold`` get a ``related`` edge with the
    # cosine similarity as the edge weight. No-op when embedding service is
    # unavailable.
    auto_link_threshold: float = Field(default=0.7)
    auto_link_top_k: int = Field(default=5)

    # Phase 5b fine-grained keyword auto-edge (docs/phase5b/FINE_EDGE_PLAN.md).
    # When enabled, create_knowledge goes through ``_auto_link_v2``: jieba
    # keywords → FTS5 recall → whole-doc cosine gate → ``auto_related`` edge
    # with initial weight 0.3. When False, falls back to ``_auto_link_by_vector``
    # (the Phase 4 behavior) for regression-gate equivalence.
    fine_edge_enabled: bool = Field(default=True)
    fine_edge_top_keywords: int = Field(default=20)
    fine_edge_fts_limit: int = Field(default=10)
    fine_edge_whole_floor: float = Field(default=0.3)
    # Phase 5b M2: when True, feedback_knowledge(helpful/misleading) re-weights
    # every auto_related edge touching the target knowledge using the sigmoid-
    # like saturation formula in FINE_EDGE_PLAN §2.3. False keeps every
    # auto_related edge at its creation-time weight.
    edge_feedback_propagation: bool = Field(default=True)

    # Phase 3 write-gate thresholds (TECH_PLAN §2.5, tech_research.md §11).
    write_gate_title_similarity_threshold: float = Field(default=0.85)
    write_gate_title_jaccard_threshold: float = Field(default=0.7)
    write_gate_semantic_similarity_threshold: float = Field(default=0.92)
    write_gate_min_content_chars: int = Field(default=50)
    write_gate_prefilter_topk: int = Field(default=50)
    write_gate_latency_budget_ms: int = Field(default=50)

    # M2 freshness decay parameters (TECH_PLAN §2.5 / §6).
    freshness_lambda_by_claim_type: dict = Field(
        default_factory=lambda: {
            "fact": 0.003,
            "decision": 0.007,
            "procedure": 0.015,
            "hypothesis": 0.02,
        }
    )
    freshness_floor_beta: float = Field(default=0.3)

    # M3 state-machine thresholds (TECH_PLAN §4.2 / §9). A row transitions
    # active → stale only when BOTH no_update_days and no_access_days have
    # elapsed — single-condition aging stays active.
    stale_thresholds_by_claim_type: dict = Field(
        default_factory=lambda: {
            "fact":       {"no_update_days": 180, "no_access_days": 90},
            "decision":   {"no_update_days": 120, "no_access_days": 60},
            "procedure":  {"no_update_days": 60,  "no_access_days": 30},
            "hypothesis": {"no_update_days": 30,  "no_access_days": 14},
        }
    )
    stale_penalty_multiplier: float = Field(default=0.3)
    last_accessed_touch_interval_s: int = Field(default=60)

    # M4 feedback loop + verification_mult (TECH_PLAN §5 / §9.5).
    feedback_sample_floor: int = Field(default=3)
    feedback_misleading_weight: float = Field(default=2.0)
    feedback_window_days: int = Field(default=30)
    feedback_dedup_hours: int = Field(default=24)
    feedback_reason_max_chars: int = Field(default=500)
    verification_mult_low: float = Field(default=0.7)
    verification_mult_high: float = Field(default=1.3)

    # P3b context-aware rank boosts (TECH_PLAN §2.5 / §9). Keyed by task_context
    # → claim_type → multiplier. Applied on top of authority rerank when
    # context_aware_rank_enabled is True. Default off until A/B validates.
    task_context_boosts: dict = Field(
        default_factory=lambda: {
            "coding": {"procedure": 1.3, "fact": 1.1},
            "debug": {"contradicts_edge": 1.4, "fact": 1.2, "procedure": 1.2},
            "decision": {"decision": 1.3, "fact": 1.1},
            "onboarding": {"fact": 1.2, "decision": 1.1},
            "general": {},
        }
    )

    model_config = SettingsConfigDict(
        env_prefix="MNEMO_",
        extra="ignore",
        protected_namespaces=("settings_",),
    )

    @property
    def data_dir_path(self) -> Path:
        if self.data_dir:
            return Path(self.data_dir)
        return resolve_data_dir()

    @property
    def database_path(self) -> Path:
        db_path = self.data_dir_path / DATABASE_NAME
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return db_path

    @property
    def database_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.database_path}"
