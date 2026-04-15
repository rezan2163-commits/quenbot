from __future__ import annotations

import asyncio
import json
import logging
import math
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

try:
    import optuna
except Exception:
    optuna = None

from event_bus import Event, EventBus, EventType

logger = logging.getLogger("quenbot.efom")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return utc_now()
    return utc_now()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        numeric = float(value)
        if math.isfinite(numeric):
            return numeric
    except Exception:
        pass
    return default


def _ensure_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


@dataclass
class ContextualTradeSnapshot:
    simulation_id: int
    signal_id: Optional[int]
    symbol: str
    timestamp: str
    trade_direction: str
    pnl: float
    pnl_pct: float
    hurst_exponent: float
    shannon_entropy: float
    fdi: float
    vpin_score: float
    volatility: float
    market_regime: str
    signal_type: str
    source: str
    source_model: str
    metadata: Dict[str, Any]

    def to_record(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["metadata"] = json.dumps(self.metadata, ensure_ascii=True, sort_keys=True)
        return payload


class RuntimeConfigStore:
    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or Path(os.getenv(
            "QUENBOT_EFOM_CONFIG_PATH",
            Path(__file__).resolve().parent / "efom_data" / "runtime_config.json",
        ))
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

    def ensure_default(self) -> Dict[str, Any]:
        if self.config_path.exists():
            return self._read_existing()
        payload = {
            "version": 1,
            "generated_at": utc_now().isoformat(),
            "entry_filters": {
                "hurst_min": 0.55,
                "entropy_max": 0.68,
                "fdi_max": 1.52,
                "vpin_max": 0.58,
                "volatility_max": 0.025,
            },
            "bayesian_best": {},
            "runtime_overrides": {
                "QUENBOT_MAMIS_BASE_POSITION_SIZE": os.getenv("QUENBOT_MAMIS_BASE_POSITION_SIZE", "250"),
                "QUENBOT_ERIFE_HURST_THRESHOLD": "0.55",
                "QUENBOT_ERIFE_ENTROPY_THRESHOLD": "0.68",
                "QUENBOT_ERIFE_FDI_THRESHOLD": "1.52",
            },
        }
        self.write(payload)
        return payload

    def load(self) -> Dict[str, Any]:
        self.ensure_default()
        return self._read_existing()

    def _read_existing(self) -> Dict[str, Any]:
        try:
            return json.loads(self.config_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {
                "version": 1,
                "generated_at": utc_now().isoformat(),
                "entry_filters": {
                    "hurst_min": 0.55,
                    "entropy_max": 0.68,
                    "fdi_max": 1.52,
                    "vpin_max": 0.58,
                    "volatility_max": 0.025,
                },
                "bayesian_best": {},
                "runtime_overrides": {
                    "QUENBOT_MAMIS_BASE_POSITION_SIZE": os.getenv("QUENBOT_MAMIS_BASE_POSITION_SIZE", "250"),
                    "QUENBOT_ERIFE_HURST_THRESHOLD": "0.55",
                    "QUENBOT_ERIFE_ENTROPY_THRESHOLD": "0.68",
                    "QUENBOT_ERIFE_FDI_THRESHOLD": "1.52",
                },
            }
            self.write(payload)
            return payload

    def write(self, payload: Dict[str, Any]) -> None:
        tmp_path = self.config_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(self.config_path)


def apply_efom_runtime_overrides(config_path: Optional[Path] = None) -> Dict[str, Any]:
    store = RuntimeConfigStore(config_path=config_path)
    payload = store.ensure_default()
    applied: Dict[str, str] = {}
    for key, value in (payload.get("runtime_overrides") or {}).items():
        if value is None:
            continue
        os.environ[str(key)] = str(value)
        applied[str(key)] = str(value)
    return {
        "config_path": str(store.config_path),
        "applied_overrides": applied,
        "generated_at": payload.get("generated_at"),
    }


class MarketStateEstimator:
    def __init__(self, db):
        self.db = db
        self._latest_mamis_bar_by_symbol: Dict[str, Dict[str, Any]] = {}

    async def on_microstructure_bar(self, event: Event):
        payload = event.data or {}
        symbol = str(payload.get("symbol", "")).upper()
        if symbol:
            self._latest_mamis_bar_by_symbol[symbol] = payload

    async def build_snapshot(self, closed_trade: Dict[str, Any]) -> ContextualTradeSnapshot:
        symbol = str(closed_trade.get("symbol", "")).upper()
        exit_time = _parse_dt(closed_trade.get("exit_time") or closed_trade.get("timestamp"))
        trade_direction = str(closed_trade.get("side") or "long").lower()
        pnl_pct = _safe_float(closed_trade.get("pnl_pct"), 0.0)
        signal_meta = _ensure_dict(closed_trade.get("signal_metadata"))
        simulation_meta = _ensure_dict(closed_trade.get("simulation_metadata"))
        recent_trades = await self._get_reference_trades(symbol, exit_time)
        prices = np.asarray([_safe_float(item.get("price"), 0.0) for item in recent_trades if _safe_float(item.get("price"), 0.0) > 0], dtype=np.float64)
        returns = np.diff(np.log(np.clip(prices, 1e-9, None))) if prices.size > 3 else np.asarray([], dtype=np.float64)

        hurst = self._estimate_hurst(prices)
        entropy = self._estimate_shannon_entropy(returns)
        fdi = self._estimate_fdi(prices)
        volatility = self._estimate_volatility(returns)
        vpin_score = self._resolve_vpin_score(symbol, exit_time, recent_trades)
        market_regime = self._classify_regime(hurst, entropy, fdi, volatility)

        return ContextualTradeSnapshot(
            simulation_id=int(closed_trade.get("simulation_id") or closed_trade.get("id") or 0),
            signal_id=closed_trade.get("signal_id"),
            symbol=symbol,
            timestamp=exit_time.isoformat(),
            trade_direction=trade_direction,
            pnl=_safe_float(closed_trade.get("pnl"), 0.0),
            pnl_pct=pnl_pct,
            hurst_exponent=hurst,
            shannon_entropy=entropy,
            fdi=fdi,
            vpin_score=vpin_score,
            volatility=volatility,
            market_regime=market_regime,
            signal_type=str(closed_trade.get("signal_type") or simulation_meta.get("signal_type") or "unknown"),
            source=str(signal_meta.get("source") or signal_meta.get("signal_provider") or simulation_meta.get("source") or "unknown"),
            source_model=str(signal_meta.get("source_model") or simulation_meta.get("source_model") or "unknown"),
            metadata={
                "entry_price": _safe_float(closed_trade.get("entry_price"), 0.0),
                "exit_price": _safe_float(closed_trade.get("exit_price"), 0.0),
                "signal_metadata": signal_meta,
                "simulation_metadata": simulation_meta,
                "trade_count": len(recent_trades),
            },
        )

    async def _get_reference_trades(self, symbol: str, exit_time: datetime, limit: int = 240) -> List[Dict[str, Any]]:
        db_exit_time = exit_time.astimezone(timezone.utc).replace(tzinfo=None) if exit_time.tzinfo else exit_time
        rows = await self.db.fetch(
            """
            SELECT price, quantity, side, timestamp
            FROM trades
            WHERE symbol = $1 AND timestamp <= $2
            ORDER BY timestamp DESC
            LIMIT $3
            """,
            symbol,
            db_exit_time,
            limit,
        )
        if rows:
            return list(reversed(rows))
        fallback = await self.db.fetch(
            """
            SELECT price, quantity, side, timestamp
            FROM trades
            WHERE symbol = $1
            ORDER BY timestamp DESC
            LIMIT $2
            """,
            symbol,
            limit,
        )
        return list(reversed(fallback))

    def _resolve_vpin_score(self, symbol: str, exit_time: datetime, recent_trades: List[Dict[str, Any]]) -> float:
        bar = self._latest_mamis_bar_by_symbol.get(symbol)
        if bar:
            ended_at = _parse_dt(bar.get("ended_at"))
            if abs((exit_time - ended_at).total_seconds()) <= 900:
                return max(0.0, min(1.0, _safe_float(bar.get("vpin"), 0.0)))

        buy_volume = 0.0
        sell_volume = 0.0
        for trade in recent_trades[-80:]:
            notion = _safe_float(trade.get("price"), 0.0) * _safe_float(trade.get("quantity"), 0.0)
            if str(trade.get("side", "buy")).lower() == "buy":
                buy_volume += notion
            else:
                sell_volume += notion
        total = buy_volume + sell_volume
        if total <= 0:
            return 0.0
        return min(1.0, abs(buy_volume - sell_volume) / total)

    def _estimate_hurst(self, prices: np.ndarray) -> float:
        if prices.size < 20:
            return 0.5
        lags = range(2, min(20, prices.size // 2))
        tau: List[float] = []
        valid_lags: List[int] = []
        for lag in lags:
            diff = prices[lag:] - prices[:-lag]
            std = np.std(diff)
            if std > 0:
                tau.append(math.sqrt(std))
                valid_lags.append(lag)
        if len(tau) < 2:
            return 0.5
        hurst = np.polyfit(np.log(valid_lags), np.log(tau), 1)[0] * 2.0
        return float(max(0.0, min(1.0, hurst)))

    def _estimate_shannon_entropy(self, returns: np.ndarray, bins: int = 12) -> float:
        if returns.size < 10:
            return 0.0
        hist, _ = np.histogram(returns, bins=bins)
        probs = hist / max(np.sum(hist), 1)
        probs = probs[probs > 0]
        entropy = -np.sum(probs * np.log(probs))
        return float(entropy / math.log(bins)) if bins > 1 else 0.0

    def _estimate_fdi(self, prices: np.ndarray) -> float:
        if prices.size < 10:
            return 1.5
        min_price = float(np.min(prices))
        max_price = float(np.max(prices))
        scale = max(max_price - min_price, 1e-9)
        normalized = (prices - min_price) / scale
        dx = 1.0 / max(len(normalized) - 1, 1)
        curve_length = np.sum(np.sqrt(np.diff(normalized) ** 2 + dx ** 2))
        if curve_length <= 0:
            return 1.5
        fdi = 1.0 + math.log(curve_length) / math.log(2 * max(len(normalized) - 1, 2))
        return float(max(1.0, min(2.0, fdi)))

    def _estimate_volatility(self, returns: np.ndarray) -> float:
        if returns.size < 2:
            return 0.0
        return float(np.std(returns) * math.sqrt(len(returns)))

    def _classify_regime(self, hurst: float, entropy: float, fdi: float, volatility: float) -> str:
        if volatility >= 0.03 or entropy >= 0.82:
            return "HIGH_VOLATILITY"
        if hurst >= 0.58 and fdi <= 1.45:
            return "TRENDING"
        if hurst <= 0.45 and entropy >= 0.55:
            return "MEAN_REVERTING"
        return "TRANSITIONAL"


class ContextualTradeLogger:
    COLUMNS = [
        "simulation_id",
        "signal_id",
        "symbol",
        "timestamp",
        "trade_direction",
        "pnl",
        "pnl_pct",
        "hurst_exponent",
        "shannon_entropy",
        "fdi",
        "vpin_score",
        "volatility",
        "market_regime",
        "signal_type",
        "source",
        "source_model",
        "metadata",
    ]

    def __init__(self, estimator: MarketStateEstimator, csv_path: Path, critique_interval: int = 100):
        self.estimator = estimator
        self.csv_path = csv_path
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        self.critique_interval = max(5, int(critique_interval))
        self.logged_trades = 0
        self.last_snapshot: Optional[ContextualTradeSnapshot] = None
        self.last_processed_simulation_id = 0
        self._io_lock = asyncio.Lock()

    async def initialize_cursor(self) -> None:
        if not self.csv_path.exists() or self.csv_path.stat().st_size == 0:
            return
        try:
            df = await asyncio.to_thread(pd.read_csv, self.csv_path)
            if not df.empty and "simulation_id" in df.columns:
                self.last_processed_simulation_id = int(df["simulation_id"].max())
                self.logged_trades = int(len(df))
        except Exception as exc:
            logger.warning("EFOM logger cursor restore failed: %s", exc)

    async def capture_trade(self, closed_trade: Dict[str, Any]) -> ContextualTradeSnapshot:
        snapshot = await self.estimator.build_snapshot(closed_trade)
        record = snapshot.to_record()
        async with self._io_lock:
            await asyncio.to_thread(self._append_record, record)
            self.logged_trades += 1
            self.last_processed_simulation_id = max(self.last_processed_simulation_id, snapshot.simulation_id)
            self.last_snapshot = snapshot
        return snapshot

    def _append_record(self, record: Dict[str, Any]) -> None:
        df = pd.DataFrame([record], columns=self.COLUMNS)
        has_header = not self.csv_path.exists() or self.csv_path.stat().st_size == 0
        df.to_csv(self.csv_path, mode="a", header=has_header, index=False)


class CriticAgent:
    def __init__(self, csv_path: Path, reports_dir: Path, llm_client=None, analysis_window: int = 300):
        self.csv_path = csv_path
        self.reports_dir = reports_dir
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.llm_client = llm_client
        self.analysis_window = max(50, int(analysis_window))
        self.last_report: Optional[Dict[str, Any]] = None

    async def generate_post_mortem(self) -> Dict[str, Any]:
        df = await self._load_recent_trades()
        report = self._build_heuristic_report(df)
        if self.llm_client:
            llm_report = await self._augment_with_llm(report, df)
            if llm_report:
                report = self._merge_reports(report, llm_report)
        report["generated_at"] = utc_now().isoformat()
        report_path = self.reports_dir / "post_mortem_report.json"
        await asyncio.to_thread(report_path.write_text, json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True), "utf-8")
        self.last_report = report
        return report

    async def _load_recent_trades(self) -> pd.DataFrame:
        if not self.csv_path.exists() or self.csv_path.stat().st_size == 0:
            return pd.DataFrame(columns=ContextualTradeLogger.COLUMNS)
        df = await asyncio.to_thread(pd.read_csv, self.csv_path)
        return df.tail(self.analysis_window).copy()

    def _build_heuristic_report(self, df: pd.DataFrame) -> Dict[str, Any]:
        if df.empty:
            return {
                "summary": "No contextual trades recorded yet.",
                "sample_size": 0,
                "failure_patterns": [],
                "parameter_adjustment_suggestions": {},
            }

        numeric_cols = [
            "pnl_pct", "hurst_exponent", "shannon_entropy", "fdi", "vpin_score", "volatility",
        ]
        for col in numeric_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

        winners = df[df["pnl_pct"] > 0]
        losers = df[df["pnl_pct"] <= 0]
        failure_patterns: List[Dict[str, Any]] = []
        suggestions: Dict[str, Any] = {}

        if not losers.empty:
            if float((losers["hurst_exponent"] < 0.45).mean()) >= 0.45:
                failure_patterns.append({
                    "condition": "hurst_exponent < 0.45",
                    "impact": "Losses cluster in mean-reverting regimes while directional trades remain active.",
                })
                suggestions["suggested_hurst_trend_min"] = round(max(0.55, float(winners["hurst_exponent"].median()) if not winners.empty else 0.6), 4)

            if float((losers["shannon_entropy"] > 0.72).mean()) >= 0.40:
                failure_patterns.append({
                    "condition": "shannon_entropy > 0.72",
                    "impact": "High-noise states degrade signal quality and reduce post-trade expectancy.",
                })
                win_entropy = float(winners["shannon_entropy"].quantile(0.75)) if not winners.empty else 0.68
                suggestions["suggested_entropy_max"] = round(min(0.9, max(0.35, win_entropy)), 4)

            if float((losers["fdi"] > 1.55).mean()) >= 0.35:
                failure_patterns.append({
                    "condition": "fdi > 1.55",
                    "impact": "Fractal dimension indicates choppy conditions; trend assumptions break down.",
                })
                win_fdi = float(winners["fdi"].quantile(0.75)) if not winners.empty else 1.5
                suggestions["suggested_fdi_max"] = round(min(1.9, max(1.1, win_fdi)), 4)

            if float((losers["vpin_score"] > 0.60).mean()) >= 0.40:
                failure_patterns.append({
                    "condition": "vpin_score > 0.60",
                    "impact": "Toxic flow dominates when trades are opened; adverse selection rises.",
                })
                win_vpin = float(winners["vpin_score"].quantile(0.70)) if not winners.empty else 0.58
                suggestions["suggested_vpin_max"] = round(min(0.95, max(0.15, win_vpin)), 4)

            if float((losers["volatility"] > 0.03).mean()) >= 0.35:
                failure_patterns.append({
                    "condition": "volatility > 0.03",
                    "impact": "Regime transitions and elevated volatility destabilize fixed exits.",
                })
                win_vol = float(winners["volatility"].quantile(0.80)) if not winners.empty else 0.025
                suggestions["suggested_volatility_max"] = round(min(0.20, max(0.002, win_vol)), 6)

        if winners.empty:
            suggestions.setdefault("suggested_position_size_scale", 0.75)
            suggestions.setdefault("suggested_take_profit_scale", 0.95)
            suggestions.setdefault("suggested_stop_loss_scale", 0.90)
        else:
            win_rate = float((df["pnl_pct"] > 0).mean())
            suggestions.setdefault("suggested_position_size_scale", round(0.85 if win_rate < 0.5 else 1.05, 4))
            suggestions.setdefault("suggested_take_profit_scale", round(1.05 if float(winners["pnl_pct"].median()) > abs(float(losers["pnl_pct"].median() if not losers.empty else 0)) else 0.98, 4))
            suggestions.setdefault("suggested_stop_loss_scale", round(0.92 if not losers.empty and abs(float(losers["pnl_pct"].median())) > float(winners["pnl_pct"].median()) else 1.02, 4))

        regime_summary = (
            df.groupby("market_regime")["pnl_pct"].agg(["count", "mean"]).reset_index().to_dict(orient="records")
        )

        return {
            "summary": f"Analyzed {len(df)} contextual trades; win rate={float((df['pnl_pct'] > 0).mean()) * 100:.1f}%.",
            "sample_size": int(len(df)),
            "regime_summary": regime_summary,
            "failure_patterns": failure_patterns,
            "parameter_adjustment_suggestions": suggestions,
        }

    async def _augment_with_llm(self, heuristic_report: Dict[str, Any], df: pd.DataFrame) -> Optional[Dict[str, Any]]:
        sample_rows = df.tail(25).to_dict(orient="records")
        prompt = json.dumps({
            "heuristic_report": heuristic_report,
            "recent_trades": sample_rows,
        }, ensure_ascii=True)
        system = (
            "You are Critic Agent for an evolutionary trading feedback module. "
            "Return strict JSON with keys: summary, failure_patterns, parameter_adjustment_suggestions. "
            "Each failure pattern must connect pnl outcomes with regime metrics such as hurst_exponent, shannon_entropy, fdi, vpin_score, volatility."
        )
        try:
            response = await self.llm_client.generate(
                prompt=prompt,
                system=system,
                temperature=0.1,
                json_mode=True,
                timeout_override=35,
                max_retries_override=0,
                prefer_fast_fail=True,
            )
            parsed = response.as_json() if response.success else None
            return parsed if isinstance(parsed, dict) else None
        except Exception as exc:
            logger.debug("EFOM critic LLM augmentation skipped: %s", exc)
            return None

    def _merge_reports(self, heuristic: Dict[str, Any], llm_report: Dict[str, Any]) -> Dict[str, Any]:
        merged = dict(heuristic)
        if llm_report.get("summary"):
            merged["summary"] = str(llm_report["summary"])
        merged["failure_patterns"] = llm_report.get("failure_patterns") or heuristic.get("failure_patterns") or []
        suggestions = dict(heuristic.get("parameter_adjustment_suggestions") or {})
        suggestions.update(llm_report.get("parameter_adjustment_suggestions") or {})
        merged["parameter_adjustment_suggestions"] = suggestions
        return merged


class HyperparameterOptimizer:
    def __init__(self, csv_path: Path, store: RuntimeConfigStore, reports_dir: Path, n_trials: int = 30):
        self.csv_path = csv_path
        self.store = store
        self.reports_dir = reports_dir
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.n_trials = max(10, int(n_trials))
        self.last_result: Optional[Dict[str, Any]] = None

    async def optimize(self, critic_report: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if optuna is None:
            logger.warning("EFOM optimizer skipped: optuna not installed")
            return {
                "status": "skipped",
                "reason": "optuna_missing",
                "config_path": str(self.store.config_path),
            }
        if not self.csv_path.exists() or self.csv_path.stat().st_size == 0:
            return None
        df = await asyncio.to_thread(pd.read_csv, self.csv_path)
        if len(df) < 30:
            return {
                "status": "skipped",
                "reason": "insufficient_samples",
                "sample_size": int(len(df)),
            }

        numeric_cols = ["pnl_pct", "hurst_exponent", "shannon_entropy", "fdi", "vpin_score", "volatility"]
        for col in numeric_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        suggestions = critic_report.get("parameter_adjustment_suggestions") or {}

        def objective(trial: "optuna.Trial") -> float:
            hurst_min = trial.suggest_float(
                "hurst_min",
                max(0.20, _safe_float(suggestions.get("suggested_hurst_trend_min"), 0.55) - 0.20),
                min(0.90, _safe_float(suggestions.get("suggested_hurst_trend_min"), 0.55) + 0.20),
            )
            entropy_max = trial.suggest_float(
                "entropy_max",
                0.25,
                min(0.95, max(0.35, _safe_float(suggestions.get("suggested_entropy_max"), 0.68) + 0.12)),
            )
            fdi_max = trial.suggest_float(
                "fdi_max",
                1.15,
                min(1.95, max(1.25, _safe_float(suggestions.get("suggested_fdi_max"), 1.52) + 0.18)),
            )
            vpin_max = trial.suggest_float(
                "vpin_max",
                0.15,
                min(0.98, max(0.30, _safe_float(suggestions.get("suggested_vpin_max"), 0.58) + 0.18)),
            )
            volatility_max = trial.suggest_float(
                "volatility_max",
                0.002,
                min(0.20, max(0.01, _safe_float(suggestions.get("suggested_volatility_max"), 0.025) + 0.02)),
            )
            take_profit_scale = trial.suggest_float("take_profit_scale", 0.85, 1.20)
            stop_loss_scale = trial.suggest_float("stop_loss_scale", 0.80, 1.15)
            position_size_scale = trial.suggest_float("position_size_scale", 0.55, 1.25)

            filtered = df[
                (df["hurst_exponent"] >= hurst_min) &
                (df["shannon_entropy"] <= entropy_max) &
                (df["fdi"] <= fdi_max) &
                (df["vpin_score"] <= vpin_max) &
                (df["volatility"] <= volatility_max)
            ]

            if len(filtered) < 12:
                return -1e6

            pnl = filtered["pnl_pct"].to_numpy(dtype=float)
            adjusted = pnl * position_size_scale
            adjusted = np.where(adjusted >= 0, adjusted * take_profit_scale, adjusted * stop_loss_scale)
            sharpe = self._sharpe(adjusted)
            sortino = self._sortino(adjusted)
            coverage = len(filtered) / max(len(df), 1)
            trial.set_user_attr("coverage", coverage)
            trial.set_user_attr("sharpe", sharpe)
            trial.set_user_attr("sortino", sortino)
            return (sharpe * 0.55) + (sortino * 0.35) + (coverage * 0.10)

        study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=42))
        await asyncio.to_thread(study.optimize, objective, self.n_trials)

        best_params = study.best_trial.params
        best_metrics = {
            "objective": float(study.best_value),
            "coverage": float(study.best_trial.user_attrs.get("coverage", 0.0)),
            "sharpe_ratio": float(study.best_trial.user_attrs.get("sharpe", 0.0)),
            "sortino_ratio": float(study.best_trial.user_attrs.get("sortino", 0.0)),
        }

        runtime_payload = self.store.load()
        runtime_payload.update({
            "generated_at": utc_now().isoformat(),
            "entry_filters": {
                "hurst_min": round(_safe_float(best_params.get("hurst_min"), 0.55), 6),
                "entropy_max": round(_safe_float(best_params.get("entropy_max"), 0.68), 6),
                "fdi_max": round(_safe_float(best_params.get("fdi_max"), 1.52), 6),
                "vpin_max": round(_safe_float(best_params.get("vpin_max"), 0.58), 6),
                "volatility_max": round(_safe_float(best_params.get("volatility_max"), 0.025), 6),
            },
            "bayesian_best": {
                **{key: round(_safe_float(value), 6) for key, value in best_params.items()},
                **best_metrics,
                "trials": int(len(study.trials)),
            },
            "runtime_overrides": {
                "QUENBOT_MAMIS_BASE_POSITION_SIZE": str(round(float(os.getenv("QUENBOT_MAMIS_BASE_POSITION_SIZE", "250")) * _safe_float(best_params.get("position_size_scale"), 1.0), 2)),
                "QUENBOT_ERIFE_HURST_THRESHOLD": str(round(_safe_float(best_params.get("hurst_min"), 0.55), 6)),
                "QUENBOT_ERIFE_ENTROPY_THRESHOLD": str(round(_safe_float(best_params.get("entropy_max"), 0.68), 6)),
                "QUENBOT_ERIFE_FDI_THRESHOLD": str(round(_safe_float(best_params.get("fdi_max"), 1.52), 6)),
                "QUENBOT_EFOM_VPIN_FILTER_MAX": str(round(_safe_float(best_params.get("vpin_max"), 0.58), 6)),
                "QUENBOT_EFOM_VOLATILITY_FILTER_MAX": str(round(_safe_float(best_params.get("volatility_max"), 0.025), 6)),
            },
            "critic_suggestions": critic_report.get("parameter_adjustment_suggestions") or {},
        })
        self.store.write(runtime_payload)

        trials_payload = [
            {
                "number": trial.number,
                "value": trial.value,
                "params": trial.params,
                "coverage": trial.user_attrs.get("coverage", 0.0),
                "sharpe": trial.user_attrs.get("sharpe", 0.0),
                "sortino": trial.user_attrs.get("sortino", 0.0),
            }
            for trial in study.trials
            if trial.value is not None
        ]
        trials_path = self.reports_dir / "optuna_trials.json"
        await asyncio.to_thread(trials_path.write_text, json.dumps(trials_payload, ensure_ascii=True, indent=2), "utf-8")

        result = {
            "status": "optimized",
            "config_path": str(self.store.config_path),
            "trials_path": str(trials_path),
            "best_parameters": runtime_payload.get("bayesian_best", {}),
            "entry_filters": runtime_payload.get("entry_filters", {}),
            "runtime_overrides": runtime_payload.get("runtime_overrides", {}),
        }
        self.last_result = result
        return result

    def _sharpe(self, pnl: np.ndarray) -> float:
        if pnl.size < 2:
            return 0.0
        std = float(np.std(pnl))
        if std <= 1e-9:
            return 0.0
        return float(np.mean(pnl) / std * math.sqrt(pnl.size))

    def _sortino(self, pnl: np.ndarray) -> float:
        if pnl.size < 2:
            return 0.0
        downside = pnl[pnl < 0]
        if downside.size == 0:
            return float(np.mean(pnl) * math.sqrt(pnl.size))
        downside_std = float(np.std(downside))
        if downside_std <= 1e-9:
            return 0.0
        return float(np.mean(pnl) / downside_std * math.sqrt(pnl.size))


class EvolutionaryFeedbackOptimizationModule:
    def __init__(self, db, event_bus: EventBus, llm_client=None):
        self.db = db
        self.event_bus = event_bus
        self.llm_client = llm_client
        self.base_dir = Path(os.getenv("QUENBOT_EFOM_DIR", Path(__file__).resolve().parent / "efom_data"))
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.csv_path = self.base_dir / "contextual_trade_log.csv"
        self.reports_dir = self.base_dir / "reports"
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_store = RuntimeConfigStore(self.base_dir / "runtime_config.json")
        self.estimator = MarketStateEstimator(db)
        self.trade_logger = ContextualTradeLogger(
            estimator=self.estimator,
            csv_path=self.csv_path,
            critique_interval=int(os.getenv("QUENBOT_EFOM_CRITIQUE_INTERVAL", "100")),
        )
        self.critic = CriticAgent(
            csv_path=self.csv_path,
            reports_dir=self.reports_dir,
            llm_client=llm_client,
            analysis_window=int(os.getenv("QUENBOT_EFOM_ANALYSIS_WINDOW", "300")),
        )
        self.optimizer = HyperparameterOptimizer(
            csv_path=self.csv_path,
            store=self.runtime_store,
            reports_dir=self.reports_dir,
            n_trials=int(os.getenv("QUENBOT_EFOM_OPTUNA_TRIALS", "30")),
        )
        self.running = False
        self._analysis_queue: asyncio.Queue = asyncio.Queue(maxsize=2)
        self._latest_report: Optional[Dict[str, Any]] = None
        self._latest_optimization: Optional[Dict[str, Any]] = None
        self._optimizations_run = 0
        self._last_activity: Optional[datetime] = None
        self._db_poll_interval = max(5.0, float(os.getenv("QUENBOT_EFOM_DB_POLL_INTERVAL", "20")))
        self._bootstrap_limit = max(0, int(os.getenv("QUENBOT_EFOM_BOOTSTRAP_LIMIT", "200")))

    async def initialize(self) -> None:
        self.runtime_store.ensure_default()
        await self.trade_logger.initialize_cursor()
        self.event_bus.subscribe(EventType.MICROSTRUCTURE_BAR, self.estimator.on_microstructure_bar)
        if self.trade_logger.logged_trades == 0 and self._bootstrap_limit > 0:
            await self._bootstrap_contextual_history(self._bootstrap_limit)

    async def start(self) -> None:
        self.running = True
        await asyncio.gather(
            self._poll_closed_trades_loop(),
            self._analysis_loop(),
        )

    async def stop(self) -> None:
        self.running = False
        try:
            self._analysis_queue.put_nowait(None)
        except Exception:
            pass

    async def health_check(self) -> Dict[str, Any]:
        return {
            "healthy": True,
            "last_activity": self._last_activity.isoformat() if self._last_activity else None,
            "logged_trades": self.trade_logger.logged_trades,
            "last_processed_simulation_id": self.trade_logger.last_processed_simulation_id,
            "optimizations_run": self._optimizations_run,
            "config_path": str(self.runtime_store.config_path),
            "latest_report": self._latest_report,
            "latest_optimization": self._latest_optimization,
            "optuna_available": optuna is not None,
        }

    async def _bootstrap_contextual_history(self, limit: int) -> None:
        rows = await self.db.fetch(
            """
            SELECT
                sim.id AS simulation_id,
                sim.signal_id,
                sim.symbol,
                sim.side,
                sim.pnl,
                sim.pnl_pct,
                sim.entry_price,
                sim.exit_price,
                sim.entry_time,
                sim.exit_time,
                sim.market_type,
                sim.metadata AS simulation_metadata,
                sig.signal_type,
                sig.metadata AS signal_metadata
            FROM simulations sim
            LEFT JOIN signals sig ON sig.id = sim.signal_id
            WHERE sim.status = 'closed'
            ORDER BY sim.id DESC
            LIMIT $1
            """,
            limit,
        )
        for row in reversed(rows):
            try:
                await self.trade_logger.capture_trade(row)
            except Exception as exc:
                logger.debug("EFOM bootstrap trade skipped: %s", exc)

    async def _poll_closed_trades_loop(self) -> None:
        while self.running:
            try:
                rows = await self.db.fetch(
                    """
                    SELECT
                        sim.id AS simulation_id,
                        sim.signal_id,
                        sim.symbol,
                        sim.side,
                        sim.pnl,
                        sim.pnl_pct,
                        sim.entry_price,
                        sim.exit_price,
                        sim.entry_time,
                        sim.exit_time,
                        sim.market_type,
                        sim.metadata AS simulation_metadata,
                        sig.signal_type,
                        sig.metadata AS signal_metadata
                    FROM simulations sim
                    LEFT JOIN signals sig ON sig.id = sim.signal_id
                    WHERE sim.status = 'closed' AND sim.id > $1
                    ORDER BY sim.id ASC
                    LIMIT 100
                    """,
                    self.trade_logger.last_processed_simulation_id,
                )
                for row in rows:
                    snapshot = await self.trade_logger.capture_trade(row)
                    self._last_activity = _parse_dt(snapshot.timestamp)
                    if self.trade_logger.logged_trades % self.trade_logger.critique_interval == 0:
                        await self._queue_analysis({
                            "reason": "trade_batch",
                            "logged_trades": self.trade_logger.logged_trades,
                        })
                await asyncio.sleep(self._db_poll_interval)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("EFOM closed-trade observer error: %s", exc)
                await asyncio.sleep(self._db_poll_interval)

    async def _analysis_loop(self) -> None:
        while self.running:
            trigger = await self._analysis_queue.get()
            if trigger is None:
                return
            try:
                report = await self.critic.generate_post_mortem()
                self._latest_report = report
                optimization = await self.optimizer.optimize(report)
                self._latest_optimization = optimization
                self._optimizations_run += 1
                self._last_activity = utc_now()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("EFOM analysis cycle failed: %s", exc)

    async def _queue_analysis(self, payload: Dict[str, Any]) -> None:
        if self._analysis_queue.full():
            return
        try:
            self._analysis_queue.put_nowait(payload)
        except Exception:
            pass