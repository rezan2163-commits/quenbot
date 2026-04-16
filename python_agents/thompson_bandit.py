"""
thompson_bandit.py — Strateji seçimi için Thompson Sampling
=============================================================
Her strateji (signal_type) bir kol. Beta-Bernoulli bandit: kar = başarı (1),
zarar = başarısızlık (0). `record_outcome` her barrier sonucundan sonra
güncellenir. `sample_best()` Thompson örneklemesi yaparak o an en umut verici
stratejiyi döner. Stratejist bunu kullanarak çelişen sinyaller arasında tercih
yapar.

Persistence: DB tablosu `bandit_state`. Sayaç + decay (exponential) sayesinde
regime değişikliklerinde adaptasyon sürer.
"""
from __future__ import annotations

import logging
import math
import random
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class ThompsonBandit:
    """Beta(α, β) kolları ile Thompson sampling."""

    DECAY = 0.995  # her olayda tüm arm'lara uygulanan unutma (~700 olayda yarılanır)

    def __init__(self) -> None:
        # arm -> {"alpha": float, "beta": float, "last_ts": float, "n": int}
        self.arms: Dict[str, Dict[str, float]] = {}
        self._loaded = False

    async def load(self, db) -> None:
        if self._loaded or db is None:
            return
        try:
            async with db.pool.acquire() as conn:
                rows = await conn.fetch("SELECT arm, alpha, beta, n, last_ts FROM bandit_state")
                for r in rows:
                    self.arms[r["arm"]] = {
                        "alpha": float(r["alpha"]), "beta": float(r["beta"]),
                        "last_ts": float(r["last_ts"] or 0.0), "n": int(r["n"] or 0),
                    }
            self._loaded = True
            logger.info(f"🎰 ThompsonBandit loaded {len(self.arms)} arms")
        except Exception as e:
            logger.debug(f"ThompsonBandit load skipped: {e}")

    async def persist(self, db) -> None:
        if db is None or not self.arms:
            return
        try:
            async with db.pool.acquire() as conn:
                await conn.executemany(
                    """
                    INSERT INTO bandit_state (arm, alpha, beta, n, last_ts)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (arm) DO UPDATE
                    SET alpha = EXCLUDED.alpha, beta = EXCLUDED.beta,
                        n = EXCLUDED.n, last_ts = EXCLUDED.last_ts
                    """,
                    [(arm, v["alpha"], v["beta"], v["n"], v["last_ts"])
                     for arm, v in self.arms.items()],
                )
        except Exception as e:
            logger.debug(f"ThompsonBandit persist skipped: {e}")

    def ensure_arm(self, arm: str) -> None:
        if arm not in self.arms:
            self.arms[arm] = {"alpha": 1.0, "beta": 1.0, "last_ts": time.time(), "n": 0}

    def record_outcome(self, arm: str, success: bool, weight: float = 1.0) -> None:
        self.ensure_arm(arm)
        # decay everyone
        for a in self.arms.values():
            a["alpha"] = (a["alpha"] - 1.0) * self.DECAY + 1.0
            a["beta"] = (a["beta"] - 1.0) * self.DECAY + 1.0
        a = self.arms[arm]
        if success:
            a["alpha"] += max(0.1, min(5.0, weight))
        else:
            a["beta"] += max(0.1, min(5.0, weight))
        a["n"] += 1
        a["last_ts"] = time.time()

    def sample(self, arm: str) -> float:
        self.ensure_arm(arm)
        a = self.arms[arm]
        try:
            return random.betavariate(a["alpha"], a["beta"])
        except Exception:
            return 0.5

    def sample_best(self, candidates: List[str]) -> Tuple[str, float]:
        if not candidates:
            return ("", 0.0)
        samples = [(c, self.sample(c)) for c in candidates]
        samples.sort(key=lambda x: x[1], reverse=True)
        return samples[0]

    def expected_value(self, arm: str) -> float:
        self.ensure_arm(arm)
        a = self.arms[arm]
        return a["alpha"] / max(a["alpha"] + a["beta"], 1e-9)

    def snapshot(self) -> Dict[str, Dict[str, float]]:
        return {
            arm: {
                "ev": round(self.expected_value(arm), 4),
                "alpha": round(v["alpha"], 3),
                "beta": round(v["beta"], 3),
                "n": int(v["n"]),
            } for arm, v in self.arms.items()
        }


_bandit: Optional[ThompsonBandit] = None


def get_thompson_bandit() -> ThompsonBandit:
    global _bandit
    if _bandit is None:
        _bandit = ThompsonBandit()
    return _bandit
