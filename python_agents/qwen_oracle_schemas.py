"""
qwen_oracle_schemas.py — Oracle Brain veri şemaları (§11)
===========================================================
Brain I/O için tip-güvenli dataclass'lar. Pydantic dependency yok; saf
stdlib. Hem shadow log'ları hem de ChromaDB embedding input'u için kullanılır.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Literal, Optional


DirectiveAction = Literal[
    "ADJUST_RISK",       # kelly_cap ölçekle
    "BIAS_DIRECTION",    # long/short tercih
    "HOLD_OFF",          # yeni pozisyon açma
    "TIGHTEN_STOPS",     # stop aralığını daralt
    "WIDEN_STOPS",       # stop aralığını genişlet
    "MONITOR",           # no-op, gözlem modu
    "EMERGENCY_FLAT",    # tüm pozisyonları kapat (safety_net ile birlikte)
]

DirectiveSeverity = Literal["info", "low", "medium", "high", "critical"]


@dataclass
class OracleObservation:
    """Bir anlık görüntünün içeriği — bütün kanallar + context."""
    symbol: str
    ts: float = field(default_factory=time.time)
    channels: Dict[str, float] = field(default_factory=dict)
    ifi: Optional[float] = None
    ifi_direction: Optional[float] = None
    confluence_score: Optional[float] = None
    context: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str, separators=(",", ":"))


@dataclass
class OracleDirective:
    """Brain'in çıkardığı karar direktifi. Shadow modda sadece log'lanır."""
    directive_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    ts: float = field(default_factory=time.time)
    symbol: str = ""
    action: DirectiveAction = "MONITOR"
    severity: DirectiveSeverity = "info"
    confidence: float = 0.0
    rationale: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    ttl_sec: int = 300
    source: str = "qwen_oracle_brain"
    shadow: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def expires_at(self) -> float:
        return self.ts + self.ttl_sec


@dataclass
class ReasoningTrace:
    """Brain'in düşünme zincirinin kayıt edilmiş halid (RAG input'u)."""
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    ts: float = field(default_factory=time.time)
    symbol: str = ""
    observation: Optional[Dict[str, Any]] = None
    directive: Optional[Dict[str, Any]] = None
    prompt: str = ""
    response: str = ""
    tokens_used: int = 0
    latency_ms: float = 0.0
    rag_hits: List[str] = field(default_factory=list)
    shadow: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def rag_document(self) -> str:
        """ChromaDB embedding için özet metin."""
        parts: List[str] = []
        if self.observation:
            parts.append(f"OBS {self.symbol}: {json.dumps(self.observation, default=str)[:500]}")
        if self.directive:
            parts.append(f"DIR: {json.dumps(self.directive, default=str)[:300]}")
        if self.response:
            parts.append(f"RESP: {self.response[:500]}")
        return "\n".join(parts)
