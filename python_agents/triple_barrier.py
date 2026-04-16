"""
triple_barrier.py — López de Prado Triple-Barrier labeling
===========================================================
Her aktif sinyal için üç bariyeri (Take-Profit, Stop-Loss, Time-horizon) eş zamanlı
değerlendirir. Klasik boolean `was_correct` yerine zenginleştirilmiş etiket üretir:

  barrier_hit:    'tp' | 'sl' | 'timeout'
  barrier_time_s: barrier'a ulaşma süresi (saniye)
  mfe_pct:        Maximum Favorable Excursion
  mae_pct:        Maximum Adverse Excursion
  risk_adjusted:  (actual_return - risk_free) / realized_vol  (Sharpe benzeri)

Signal metadata'sına ek alanlar: `tp_pct`, `sl_pct` (yoksa default). Bu modül
salt hesap yapar, yan etkisiz — main.py _evaluate_signal_horizons içinden çağrılır.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple


@dataclass
class BarrierResult:
    barrier_hit: str                 # 'tp' | 'sl' | 'timeout'
    barrier_time_s: float            # saniye
    final_return_pct: float          # entry → exit net getirisi
    mfe_pct: float                   # max favorable
    mae_pct: float                   # max adverse
    risk_adjusted_return: float      # Sharpe-benzeri
    confidence_factor: float         # 0..1 (mfe/mae oranına göre)

    def to_dict(self) -> Dict[str, float | str]:
        return {
            "barrier_hit": self.barrier_hit,
            "barrier_time_s": round(self.barrier_time_s, 2),
            "final_return_pct": round(self.final_return_pct * 100, 4),
            "mfe_pct": round(self.mfe_pct * 100, 4),
            "mae_pct": round(self.mae_pct * 100, 4),
            "risk_adjusted_return": round(self.risk_adjusted_return, 4),
            "confidence_factor": round(self.confidence_factor, 4),
        }


def compute_triple_barrier(
    *,
    direction: str,
    entry_price: float,
    entry_ts: float,
    path: Iterable[Tuple[float, float]],
    tp_pct: float = 0.01,
    sl_pct: float = 0.007,
    timeout_s: float = 3600.0,
) -> BarrierResult:
    """
    path: iterable of (timestamp_s, price). Entry hariç, sonraki tick'ler.
    tp_pct / sl_pct: entry'den itibaren yüzdesel bariyerler (pozitif).
    """
    if entry_price <= 0:
        return BarrierResult("timeout", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    is_long = direction == "long"
    tp_price = entry_price * (1.0 + tp_pct) if is_long else entry_price * (1.0 - tp_pct)
    sl_price = entry_price * (1.0 - sl_pct) if is_long else entry_price * (1.0 + sl_pct)

    best = entry_price
    worst = entry_price
    last_ts = entry_ts
    last_price = entry_price
    barrier_hit = "timeout"
    barrier_time = timeout_s

    returns: List[float] = []

    for ts, price in path:
        if price <= 0:
            continue
        last_ts = ts; last_price = price
        if is_long:
            best = max(best, price); worst = min(worst, price)
        else:
            best = min(best, price); worst = max(worst, price)

        returns.append((price - entry_price) / entry_price * (1 if is_long else -1))

        elapsed = max(0.0, ts - entry_ts)
        if is_long:
            if price >= tp_price:
                barrier_hit, barrier_time = "tp", elapsed; break
            if price <= sl_price:
                barrier_hit, barrier_time = "sl", elapsed; break
        else:
            if price <= tp_price:
                barrier_hit, barrier_time = "tp", elapsed; break
            if price >= sl_price:
                barrier_hit, barrier_time = "sl", elapsed; break
        if elapsed >= timeout_s:
            barrier_hit, barrier_time = "timeout", elapsed; break

    if is_long:
        final_ret = (last_price - entry_price) / entry_price
        mfe = (best - entry_price) / entry_price
        mae = (worst - entry_price) / entry_price  # negative for long when price dropped
    else:
        final_ret = (entry_price - last_price) / entry_price
        mfe = (entry_price - best) / entry_price
        mae = (entry_price - worst) / entry_price

    # Realized vol (std of per-tick returns); fallback ~ abs(final)
    if len(returns) >= 2:
        mu = sum(returns) / len(returns)
        var = sum((r - mu) ** 2 for r in returns) / max(len(returns) - 1, 1)
        vol = var ** 0.5 or 1e-6
    else:
        vol = max(abs(final_ret), 1e-6)

    risk_adj = final_ret / vol
    conf = max(0.0, min(1.0, (mfe + 1e-6) / (mfe + abs(mae) + 1e-6)))

    return BarrierResult(
        barrier_hit=barrier_hit,
        barrier_time_s=barrier_time,
        final_return_pct=final_ret,
        mfe_pct=mfe,
        mae_pct=mae,
        risk_adjusted_return=risk_adj,
        confidence_factor=conf,
    )


def summarize_barriers(results: List[BarrierResult]) -> Dict[str, float]:
    """Yığın etiket sonuçlarının özeti — meta-learning için."""
    if not results:
        return {}
    n = len(results)
    tp = sum(1 for r in results if r.barrier_hit == "tp")
    sl = sum(1 for r in results if r.barrier_hit == "sl")
    to = sum(1 for r in results if r.barrier_hit == "timeout")
    return {
        "n": n,
        "tp_rate": tp / n,
        "sl_rate": sl / n,
        "timeout_rate": to / n,
        "avg_return_pct": sum(r.final_return_pct for r in results) / n * 100,
        "avg_mfe_pct": sum(r.mfe_pct for r in results) / n * 100,
        "avg_mae_pct": sum(r.mae_pct for r in results) / n * 100,
        "avg_risk_adj": sum(r.risk_adjusted_return for r in results) / n,
        "avg_barrier_time_s": sum(r.barrier_time_s for r in results) / n,
    }
