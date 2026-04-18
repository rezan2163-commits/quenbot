"""Self-play scaffold (PR2 placeholder).

§11 Qwen Oracle Brain için ileride kullanılacak self-play senaryo iskeleti.
PR2 kapsamında sadece placeholder; gerçek senaryo üretimi PR3+ aşamasında
runtime_supervisor ve ghost_simulator_agent entegrasyonuyla gelecektir.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class SelfPlayScenario:
    scenario_id: str
    symbol: str
    regime: str
    seed_channels: Dict[str, float] = field(default_factory=dict)
    expected_action: str = "MONITOR"
    note: str = ""


async def run_scenarios(scenarios: List[SelfPlayScenario]) -> List[Dict[str, Any]]:
    """Placeholder — gerçek motor PR3'te devreye alınacak."""
    results: List[Dict[str, Any]] = []
    for sc in scenarios:
        await asyncio.sleep(0)
        results.append({
            "scenario_id": sc.scenario_id,
            "symbol": sc.symbol,
            "regime": sc.regime,
            "status": "scaffold-only",
        })
    return results


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(run_scenarios([
        SelfPlayScenario(scenario_id="demo-1", symbol="BTCUSDT", regime="trend"),
    ]))
