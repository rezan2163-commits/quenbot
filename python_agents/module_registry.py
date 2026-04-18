"""
module_registry.py — Canonical list of all observable modules.
============================================================
Every module the Mission Control watches is declared here, grouped by
"organ" (agent / brain / detector / fusion / learning / safety / runtime).

This is a pure-data module. It does not import agents or emit side effects.
Consumers: ``mission_control_aggregator`` (status + edges), mission-control
frontend (layout clusters), tests (consistency checks).

Every entry declares:
  - ``id``              stable slug (e.g. ``"hawkes_kernel_fitter"``)
  - ``display_name``    Turkish name for UI
  - ``description``     one-line Turkish description
  - ``organ``           category — drives color family and cluster layout
  - ``heartbeat_source``
        how to read liveness: ``"runtime_supervisor"``, ``"event_bus"``,
        ``"db_heartbeat"``, ``"callable"``, ``"flag_only"``
  - ``heartbeat_key``   lookup key for the chosen source
  - ``expected_period_sec``
        maximum silence before considered stale
  - ``event_signatures``
        list of ``EventType`` values this module emits — used to light up
        constellation edges and compute throughput
  - ``dependencies``    upstream module ids this module consumes from
  - ``default_state``   ``"active" | "dormant" | "flag_gated"``
  - ``flag_env``        env var that enables the module when flag-gated
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

try:  # pragma: no cover — import fallback for isolated test loading
    from event_bus import EventType
except Exception:  # pragma: no cover
    from python_agents.event_bus import EventType  # type: ignore


VALID_SOURCES = frozenset({
    "runtime_supervisor",
    "event_bus",
    "db_heartbeat",
    "callable",
    "flag_only",
})

VALID_ORGANS = frozenset({
    "agent",
    "brain",
    "detector",
    "fusion",
    "learning",
    "safety",
    "runtime",
})

VALID_STATES = frozenset({"active", "dormant", "flag_gated"})


@dataclass(frozen=True)
class ModuleSpec:
    id: str
    display_name: str
    description: str
    organ: str
    heartbeat_source: str
    heartbeat_key: str
    expected_period_sec: float
    event_signatures: tuple = ()
    dependencies: tuple = ()
    default_state: str = "active"
    flag_env: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "description": self.description,
            "organ": self.organ,
            "heartbeat_source": self.heartbeat_source,
            "heartbeat_key": self.heartbeat_key,
            "expected_period_sec": self.expected_period_sec,
            "event_signatures": list(self.event_signatures),
            "dependencies": list(self.dependencies),
            "default_state": self.default_state,
            "flag_env": self.flag_env,
        }


def _spec(
    id: str,
    display_name: str,
    description: str,
    organ: str,
    *,
    source: str,
    key: str,
    period: float,
    events: tuple = (),
    deps: tuple = (),
    state: str = "active",
    flag_env: Optional[str] = None,
) -> ModuleSpec:
    return ModuleSpec(
        id=id,
        display_name=display_name,
        description=description,
        organ=organ,
        heartbeat_source=source,
        heartbeat_key=key,
        expected_period_sec=period,
        event_signatures=events,
        dependencies=deps,
        default_state=state,
        flag_env=flag_env,
    )


# ---------------------------------------------------------------------------
# Registry — exhaustive, grouped by organ. Turkish display names, English ids.
# ---------------------------------------------------------------------------

_ALL: List[ModuleSpec] = [
    # ─── AGENTS ────────────────────────────────────────────────────────────
    _spec(
        "scout_agent", "Scout Ajan",
        "Piyasa verisini izler, fiyat/OrderBook günceller, anomali yayar.",
        "agent",
        source="event_bus", key="scout_agent", period=30,
        events=(EventType.SCOUT_PRICE_UPDATE.value, EventType.ORDER_BOOK_UPDATE.value,
                EventType.SCOUT_ANOMALY.value, EventType.AGENT_HEARTBEAT.value),
        deps=("event_bus",),
    ),
    _spec(
        "strategist_agent", "Stratejist Ajan",
        "Sinyal değerlendirir, onaylar / reddeder, karar üretir.",
        "agent",
        source="event_bus", key="strategist", period=60,
        events=(EventType.SIGNAL_GENERATED.value, EventType.SIGNAL_APPROVED.value,
                EventType.SIGNAL_REJECTED.value, EventType.AGENT_HEARTBEAT.value),
        deps=("scout_agent", "brain", "event_bus"),
    ),
    _spec(
        "ghost_simulator_agent", "Ghost Simülatör",
        "Paper-trade simülasyonu yürütür, P&L ve horizon sonuçlarını üretir.",
        "agent",
        source="event_bus", key="ghost_simulator", period=60,
        events=(EventType.SIM_OPENED.value, EventType.SIM_CLOSED.value,
                EventType.SIM_UPDATE.value, EventType.HORIZON_RESOLVED.value),
        deps=("strategist_agent", "event_bus"),
    ),
    _spec(
        "auditor_agent", "Denetçi Ajan",
        "Trade sonrası audit, correction ve RCA yayar.",
        "agent",
        source="event_bus", key="auditor", period=300,
        events=(EventType.AUDIT_COMPLETE.value, EventType.CORRECTION_APPLIED.value),
        deps=("ghost_simulator_agent", "event_bus"),
    ),
    _spec(
        "pattern_matcher_agent", "Pattern Eşleyici",
        "Fiyat desenlerini eşleştirir, pattern event'leri yayar.",
        "agent",
        source="event_bus", key="pattern_matcher", period=120,
        events=(EventType.PATTERN_MATCH.value, EventType.PATTERN_DETECTED.value,
                EventType.SIGNATURE_MATCH.value),
        deps=("scout_agent", "event_bus"),
    ),

    # ─── BRAINS ────────────────────────────────────────────────────────────
    _spec(
        "brain", "Ana Beyin",
        "LLM destekli strateji özeti ve makro sentez.",
        "brain",
        source="event_bus", key="brain", period=120,
        events=(EventType.DECISION_MADE.value, EventType.AGENT_HEARTBEAT.value),
        deps=("event_bus",),
    ),
    _spec(
        "fast_brain", "Hızlı Beyin",
        "Düşük gecikmeli tahmin ve fast-brain çıktıları.",
        "brain",
        source="event_bus", key="decision_core", period=120,
        events=(EventType.FAST_BRAIN_PREDICTION.value, EventType.FINAL_DECISION.value,
                EventType.DECISION_SHADOW.value),
        deps=("confluence_engine", "event_bus"),
    ),
    _spec(
        "gemma_decision_core", "Gemma Karar Çekirdeği",
        "Gemma modeli destekli karar stratejisi.",
        "brain",
        source="event_bus", key="gemma_decision_core", period=180,
        events=(EventType.DECISION_MADE.value,),
        deps=("event_bus",),
        state="flag_gated", flag_env="QUENBOT_GEMMA_ENABLED",
    ),
    _spec(
        "qwen_oracle_brain", "Qwen Oracle Beyin",
        "Sistem geneli direktifler ve reasoning trace üretir.",
        "brain",
        source="event_bus", key="llm_brain", period=120,
        events=(EventType.ORACLE_DIRECTIVE_ISSUED.value,
                EventType.ORACLE_REASONING_TRACE.value,
                EventType.DIRECTIVE_ACCEPTED.value),
        deps=("factor_graph_fusion", "oracle_signal_bus", "event_bus"),
    ),

    # ─── DETECTORS ─────────────────────────────────────────────────────────
    _spec(
        "microstructure", "Mikroyapı Ölçer",
        "Tick-level mikroyapı özellikleri ve bar üretimi.",
        "detector",
        source="event_bus", key="mamis", period=60,
        events=(EventType.MICROSTRUCTURE_BAR.value,
                EventType.MICROSTRUCTURE_FEATURES.value,
                EventType.MICROSTRUCTURE_CLASSIFIED.value,
                EventType.MICROSTRUCTURE_ALERT.value),
        deps=("scout_agent",),
    ),
    _spec(
        "iceberg_detector", "Iceberg Dedektör",
        "Büyük siparişlerin gizli parçalarını tespit eder.",
        "detector",
        source="event_bus", key="iceberg_detector", period=120,
        events=(EventType.ICEBERG_DETECTED.value,),
        deps=("microstructure",),
    ),
    _spec(
        "systematic_trade_detector", "Sistematik İşlem Dedektörü",
        "Spoofing ve sistematik emir kalıplarını yakalar.",
        "detector",
        source="event_bus", key="systematic_trade_detector", period=120,
        events=(EventType.SPOOF_DETECTED.value,),
        deps=("microstructure",),
    ),
    _spec(
        "signature_engine", "Path İmza Motoru",
        "Path-signature eşleşmesi üretir.",
        "detector",
        source="event_bus", key="signature_engine", period=120,
        events=(EventType.SIGNATURE_MATCH.value, EventType.PATH_SIGNATURE_MATCH.value),
        deps=("scout_agent",),
    ),
    _spec(
        "hmm_regime", "HMM Rejim Modeli",
        "Hidden Markov rejim geçişlerini izler.",
        "detector",
        source="event_bus", key="regime_hmm", period=180,
        events=(EventType.REGIME_CHANGE.value,),
        deps=("scout_agent",),
    ),
    _spec(
        "market_regime", "Piyasa Rejim",
        "Volatilite ve trend rejimini sınıflar.",
        "detector",
        source="event_bus", key="market_regime", period=180,
        events=(EventType.REGIME_CHANGE.value,),
        deps=("scout_agent",),
    ),
    _spec(
        "order_flow_imbalance", "Emir Akışı Dengesizliği",
        "OFI özelliklerini üretir.",
        "detector",
        source="event_bus", key="ofi_engine", period=60,
        events=(EventType.ORDER_FLOW_IMBALANCE.value,),
        deps=("microstructure",),
    ),
    _spec(
        "multi_horizon_signatures", "Çok Ufuk İmzaları",
        "Farklı vade imzalarını birleştirir.",
        "detector",
        source="event_bus", key="multi_horizon_engine", period=120,
        events=(EventType.MULTI_HORIZON_SIGNATURE.value,),
        deps=("signature_engine",),
    ),
    _spec(
        "cross_asset_graph", "Varlıklar Arası Graf",
        "Varlıklar arası bağımlılıkları çıkarır.",
        "detector",
        source="event_bus", key="cross_asset_engine", period=180,
        events=(EventType.CROSS_ASSET_GRAPH_UPDATED.value, EventType.LEAD_LAG_ALERT.value),
        deps=("scout_agent",),
    ),
    _spec(
        "bocpd_detector", "BOCPD Dedektör",
        "Bayesian online changepoint tespiti.",
        "detector",
        source="event_bus", key="bocpd_detector", period=120,
        events=(EventType.BOCPD_CONSENSUS_CHANGEPOINT.value,),
        deps=("scout_agent",),
    ),
    _spec(
        "hawkes_kernel_fitter", "Hawkes Çekirdek Ölçer",
        "Self-exciting order flow — whale iceberg refill sinyali.",
        "detector",
        source="event_bus", key="hawkes_kernel_fitter", period=120,
        events=(EventType.HAWKES_KERNEL_UPDATE.value,),
        deps=("scout_agent", "microstructure"),
    ),
    _spec(
        "lob_thermodynamics", "LOB Termodinamiği",
        "Order book enerji/entropi durumunu izler.",
        "detector",
        source="event_bus", key="lob_thermodynamics", period=120,
        events=(EventType.LOB_THERMODYNAMIC_STATE.value,),
        deps=("scout_agent",),
    ),
    _spec(
        "wasserstein_drift", "Wasserstein Drift",
        "Dağılım değişimlerini ölçer.",
        "detector",
        source="event_bus", key="wasserstein_drift", period=180,
        events=(EventType.DISTRIBUTION_SHIFT.value,),
        deps=("scout_agent",),
    ),
    _spec(
        "path_signature_engine", "Path Signature Motoru",
        "Path-signature özellik üretimi.",
        "detector",
        source="event_bus", key="path_signature_engine", period=180,
        events=(EventType.PATH_SIGNATURE_MATCH.value,),
        deps=("scout_agent",),
    ),
    _spec(
        "mirror_flow_analyzer", "Ayna Akış Analizcisi",
        "Karşı-borsa mirror execution tespiti.",
        "detector",
        source="event_bus", key="mirror_flow_analyzer", period=180,
        events=(EventType.MIRROR_EXECUTION_DETECTED.value,),
        deps=("scout_agent",),
    ),
    _spec(
        "topological_lob_analyzer", "Topolojik LOB Analizcisi",
        "Order book topolojik anomalileri.",
        "detector",
        source="event_bus", key="topological_lob_analyzer", period=180,
        events=(EventType.TOPOLOGICAL_ANOMALY.value,),
        deps=("scout_agent",),
    ),
    _spec(
        "causal_onchain_bridge", "On-Chain Nedensel Köprü",
        "On-chain kaynaklı nedensel sinyaller.",
        "detector",
        source="event_bus", key="causal_onchain_bridge", period=300,
        events=(EventType.ONCHAIN_CAUSAL_SIGNAL.value,),
        deps=("event_bus",),
        state="flag_gated", flag_env="QUENBOT_ONCHAIN_BRIDGE_ENABLED",
    ),

    # ─── FUSION ────────────────────────────────────────────────────────────
    _spec(
        "confluence_engine", "Confluence Motoru",
        "Dedektör çıktılarını birleşik skora indirger.",
        "fusion",
        source="event_bus", key="confluence_engine", period=60,
        events=(EventType.CONFLUENCE_SCORE.value, EventType.CONFLUENCE_WEIGHTS_ROTATED.value),
        deps=("order_flow_imbalance", "multi_horizon_signatures", "microstructure",
              "bocpd_detector", "hawkes_kernel_fitter"),
    ),
    _spec(
        "factor_graph_fusion", "Factor Graph Füzyon",
        "Oracle factor-graph füzyonu, IFI üretimi.",
        "fusion",
        source="event_bus", key="factor_graph_fusion", period=120,
        events=(EventType.INVISIBLE_FOOTPRINT_INDEX.value,),
        deps=("hawkes_kernel_fitter", "bocpd_detector", "wasserstein_drift",
              "lob_thermodynamics", "path_signature_engine"),
    ),
    _spec(
        "oracle_signal_bus", "Oracle Sinyal Barı",
        "Dedektör → beyin arası sinyal kanalı.",
        "fusion",
        source="event_bus", key="oracle_signal_bus", period=60,
        events=(EventType.ORACLE_REASONING_TRACE.value,),
        deps=("bocpd_detector", "hawkes_kernel_fitter", "lob_thermodynamics",
              "wasserstein_drift", "path_signature_engine",
              "mirror_flow_analyzer", "topological_lob_analyzer",
              "causal_onchain_bridge"),
    ),

    # ─── LEARNING ──────────────────────────────────────────────────────────
    _spec(
        "triple_barrier", "Triple Barrier",
        "Lopez de Prado triple-barrier etiketlemesi.",
        "learning",
        source="event_bus", key="triple_barrier", period=300,
        events=(EventType.BARRIER_LABELED.value,),
        deps=("ghost_simulator_agent",),
    ),
    _spec(
        "meta_labeler", "Meta Etiketleyici",
        "Sinyal kalite meta-etiketleri üretir.",
        "learning",
        source="event_bus", key="meta_labeler", period=300,
        events=(EventType.META_LABEL_DECISION.value, EventType.META_MODEL_REFIT.value),
        deps=("triple_barrier",),
    ),
    _spec(
        "conformal", "Conformal Kalibratör",
        "Conformal güven aralıkları kalibre eder.",
        "learning",
        source="event_bus", key="conformal_calibrator", period=600,
        events=(),
        deps=("meta_labeler",),
    ),
    _spec(
        "thompson_bandit", "Thompson Bandit",
        "Strateji ağırlıklarını Thompson Sampling ile günceller.",
        "learning",
        source="event_bus", key="thompson_bandit", period=300,
        events=(EventType.BANDIT_UPDATED.value,),
        deps=("meta_labeler",),
    ),
    _spec(
        "alpha_drift_monitor", "Alpha Drift Monitor",
        "Alpha sinyal driftini izler.",
        "learning",
        source="event_bus", key="alpha_drift", period=600,
        events=(EventType.DRIFT_ALERT.value,),
        deps=("meta_labeler",),
    ),
    _spec(
        "loss_autopsy", "Loss Autopsy",
        "Kaybeden işlemleri otopsi ile inceler.",
        "learning",
        source="event_bus", key="loss_autopsy", period=600,
        events=(EventType.LOSS_AUTOPSY.value,),
        deps=("ghost_simulator_agent",),
    ),
    _spec(
        "rca_engine", "RCA Motoru",
        "Kök-neden analizleri üretir.",
        "learning",
        source="event_bus", key="rca_engine", period=600,
        events=(),
        deps=("auditor_agent", "loss_autopsy"),
    ),
    _spec(
        "performance_attribution", "Performans Atfı",
        "PnL'i stratejilere atıfla dağıtır.",
        "learning",
        source="event_bus", key="performance_attribution", period=600,
        events=(),
        deps=("ghost_simulator_agent",),
    ),
    _spec(
        "online_learning", "Online Öğrenme",
        "Canlı gradient güncellemeleri uygular.",
        "learning",
        source="event_bus", key="online_learning", period=300,
        events=(EventType.EXPERIENCE_RECORDED.value,),
        deps=("meta_labeler",),
    ),

    # ─── SAFETY & RUNTIME ──────────────────────────────────────────────────
    _spec(
        "decision_router", "Karar Yönlendirici",
        "Final kararı uygun rotaya yönlendirir.",
        "safety",
        source="event_bus", key="decision_router", period=60,
        events=(EventType.FINAL_DECISION.value,),
        deps=("fast_brain", "confluence_engine"),
    ),
    _spec(
        "safety_net", "Güvenlik Ağı",
        "Drift / regression tripwire'ları; rollback sentinel.",
        "safety",
        source="callable", key="safety_net", period=120,
        events=(EventType.SAFETY_NET_TRIPPED.value, EventType.SAFETY_NET_RESET.value,
                EventType.SAFETY_NET_DRIFT_ALERT.value,
                EventType.SAFETY_NET_FS_DEGRADED.value,
                EventType.SAFETY_NET_DIRECTIVE_REGRESSION.value),
        deps=("decision_router", "directive_impact_tracker"),
    ),
    _spec(
        "runtime_supervisor", "Runtime Süpervizör",
        "Bileşen health-check'leri, heartbeat dosyası.",
        "runtime",
        source="callable", key="runtime_supervisor", period=60,
        events=(),
        deps=(),
    ),
    _spec(
        "risk_manager", "Risk Yöneticisi",
        "Boyut / risk limitleri ve RISK_* olayları.",
        "safety",
        source="event_bus", key="risk_manager", period=120,
        events=(EventType.RISK_APPROVED.value, EventType.RISK_REJECTED.value,
                EventType.RISK_ALERT.value),
        deps=("strategist_agent",),
    ),
    _spec(
        "proactive_risk", "Proaktif Risk",
        "İleriye dönük risk değerlendirmesi.",
        "safety",
        source="event_bus", key="proactive_risk", period=180,
        events=(EventType.RISK_ALERT.value,),
        deps=("risk_manager",),
    ),
    _spec(
        "metrics_exporter", "Metrik Yayıncı",
        "Prometheus metriklerini yayınlar.",
        "runtime",
        source="callable", key="metrics_exporter", period=60,
        events=(),
        deps=(),
    ),
    _spec(
        "event_bus", "Event Bus",
        "Merkezi pub/sub omurga.",
        "runtime",
        source="callable", key="event_bus", period=30,
        events=(),
        deps=(),
    ),
    _spec(
        "vector_memory", "Vektör Bellek",
        "Similarity / pattern hafızası.",
        "runtime",
        source="callable", key="vector_memory", period=600,
        events=(),
        deps=(),
    ),
    _spec(
        "directive_gatekeeper", "Direktif Gatekeeper",
        "Aşama 1 — Qwen direktiflerini filtreler.",
        "safety",
        source="callable", key="directive_gatekeeper", period=180,
        events=(EventType.DIRECTIVE_REJECTED.value, EventType.DIRECTIVE_ACCEPTED.value),
        deps=("qwen_oracle_brain",),
    ),
    _spec(
        "auto_rollback_monitor", "Oto-Rollback Monitörü",
        "Aşama 1 — bozuk direktifleri geri alır.",
        "safety",
        source="callable", key="auto_rollback_monitor", period=300,
        events=(EventType.ORACLE_AUTO_ROLLBACK.value, EventType.WARMUP_COMPLETED.value),
        deps=("directive_gatekeeper",),
    ),
    _spec(
        "directive_impact_tracker", "Direktif Etki İzleyici",
        "Aşama 2 — direktif sonrası impact ölçümü.",
        "learning",
        source="callable", key="directive_impact_tracker", period=600,
        events=(EventType.DIRECTIVE_IMPACT_MEASURED.value,),
        deps=("auto_rollback_monitor",),
    ),
    _spec(
        "emergency_lockdown", "Acil Durum Kilidi",
        "Aşama 3 — sistemi aciden kilitler.",
        "safety",
        source="callable", key="emergency_lockdown", period=300,
        events=(EventType.EMERGENCY_LOCKDOWN.value,
                EventType.EMERGENCY_LOCKDOWN_RELEASED.value),
        deps=(),
    ),
]


MODULE_REGISTRY: Dict[str, ModuleSpec] = {m.id: m for m in _ALL}


def list_modules() -> List[ModuleSpec]:
    """Return every registered module in declaration order."""
    return list(_ALL)


def list_by_organ() -> Dict[str, List[ModuleSpec]]:
    """Return modules grouped by organ, preserving declaration order."""
    out: Dict[str, List[ModuleSpec]] = {o: [] for o in VALID_ORGANS}
    for m in _ALL:
        out.setdefault(m.organ, []).append(m)
    return out


def get(module_id: str) -> Optional[ModuleSpec]:
    """Lookup a single module by id."""
    return MODULE_REGISTRY.get(module_id)


def known_event_signatures() -> frozenset:
    """Return the union of all declared event signatures across modules."""
    sigs: set = set()
    for m in _ALL:
        sigs.update(m.event_signatures)
    return frozenset(sigs)


__all__ = [
    "ModuleSpec",
    "MODULE_REGISTRY",
    "VALID_SOURCES",
    "VALID_ORGANS",
    "VALID_STATES",
    "list_modules",
    "list_by_organ",
    "get",
    "known_event_signatures",
]
