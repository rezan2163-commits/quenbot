from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ExchangeName(str, Enum):
    BINANCE = "binance"
    BYBIT = "bybit"
    MIXED = "mixed"


class MarketType(str, Enum):
    SPOT = "spot"
    FUTURES = "futures"


class CommandAction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    HOLD = "HOLD"
    WATCHLIST_UPDATE = "WATCHLIST_UPDATE"
    PAPER_TRADE = "PAPER_TRADE"
    CLEANUP = "CLEANUP"
    DIAGNOSTIC = "DIAGNOSTIC"


class CommandPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class MarketTick(BaseModel):
    symbol: str = Field(min_length=3, max_length=20)
    exchange: ExchangeName = ExchangeName.MIXED
    market_type: MarketType = MarketType.SPOT
    price: float = Field(gt=0)
    quantity: float = Field(gt=0)
    side: Literal["buy", "sell"]
    timestamp: datetime = Field(default_factory=utc_now)
    trade_id: Optional[str] = None

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.upper().strip()


class MarketFeatureSnapshot(BaseModel):
    symbol: str
    exchange: ExchangeName = ExchangeName.MIXED
    market_type: MarketType = MarketType.SPOT
    timeframe: str = "15m"
    observed_at: datetime = Field(default_factory=utc_now)
    price_series: List[float] = Field(default_factory=list)
    volume_series: List[float] = Field(default_factory=list)
    change_pct: float = 0.0
    buy_ratio: float = 0.5
    volatility: float = 0.0
    feature_vector: List[float] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("symbol")
    @classmethod
    def normalize_snapshot_symbol(cls, value: str) -> str:
        return value.upper().strip()


class PatternMatchCandidate(BaseModel):
    reference_id: str
    similarity: float = Field(ge=0.0, le=1.0)
    direction: Literal["long", "short", "neutral"] = "neutral"
    magnitude: float = 0.0
    timeframe: str = "15m"
    occurred_at: datetime = Field(default_factory=utc_now)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PatternDetectionEvent(BaseModel):
    event_type: Literal["EVENT_PATTERN_DETECTED"] = "EVENT_PATTERN_DETECTED"
    symbol: str
    exchange: ExchangeName = ExchangeName.MIXED
    market_type: MarketType = MarketType.SPOT
    timeframe: str = "15m"
    current_price: float = Field(gt=0)
    price_change_pct: float
    trigger_threshold_pct: float = 0.02
    lookback_hours: int = 24
    similarity_threshold: float = 0.5
    matches: List[PatternMatchCandidate] = Field(default_factory=list)
    triggered_at: datetime = Field(default_factory=utc_now)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("symbol")
    @classmethod
    def normalize_detected_symbol(cls, value: str) -> str:
        return value.upper().strip()


class DecisionCommand(BaseModel):
    action: CommandAction
    symbol: str = Field(default="GLOBAL")
    market_type: MarketType = MarketType.SPOT
    exchange: ExchangeName = ExchangeName.MIXED
    target_profit_pct: float = Field(default=0.02, ge=0.0, le=0.5)
    stop_loss_pct: float = Field(default=0.01, ge=0.0, le=0.5)
    estimated_duration_minutes: int = Field(default=30, ge=1, le=1440)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reasoning: str = Field(default="", max_length=4000)
    strategy: str = Field(default="pattern_follow")
    execution_mode: Literal["paper", "live"] = "paper"
    constraints: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("symbol")
    @classmethod
    def normalize_command_symbol(cls, value: str) -> str:
        return value.upper().strip()


class DecisionEnvelope(BaseModel):
    task: str
    goal: str
    strategy_summary: str
    command: DecisionCommand
    priority: CommandPriority = CommandPriority.NORMAL
    source_event: str = "pattern.match"
    reasoning_steps: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class DirectivePayload(BaseModel):
    directive: str
    requested_by: str = "dashboard"
    symbols: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ExecutionFeedback(BaseModel):
    symbol: str
    action: CommandAction
    status: Literal["queued", "paper_opened", "paper_closed", "error", "rejected"]
    pnl_pct: Optional[float] = None
    error_message: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)
    observed_at: datetime = Field(default_factory=utc_now)


class LearningExperience(BaseModel):
    symbol: str
    action: CommandAction
    outcome: Literal["success", "failure", "neutral", "error"]
    pnl_pct: float = 0.0
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reasoning: str = ""
    lessons: List[str] = Field(default_factory=list)
    context: Dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=utc_now)


class ErrorObservation(BaseModel):
    source: str
    error_type: str
    message: str
    severity: Literal["warning", "error", "critical"] = "error"
    context: Dict[str, Any] = Field(default_factory=dict)
    observed_at: datetime = Field(default_factory=utc_now)


class CommunicationLogEntry(BaseModel):
    channel: str
    source: str
    kind: Literal["event", "command", "response", "error", "directive"]
    summary: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


def decision_command_json_schema() -> Dict[str, Any]:
    return DecisionEnvelope.model_json_schema()