"""
Aşama 3 — Operator Weekly Acknowledgement CLI
=============================================

Records the operator's confirmation that the weekly strategic review has been
read and accepted. Without an ack within 7 days, the auto-degrade watchdog
falls the system back to Aşama 2 throttles.

Usage::

    python python_agents/scripts/ack_weekly.py --week 2026-16 --note "Hafta temizdi"
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, List

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

logger = logging.getLogger("ack_weekly")


def _sha256_file(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def write_ack(
    *,
    week_label: str,
    note: str,
    operator: Optional[str] = None,
    ack_dir: Optional[Path] = None,
    review_path: Optional[Path] = None,
) -> Path:
    from config import Config
    ack_dir = Path(ack_dir or getattr(Config, "WEEKLY_ACK_DIR", "python_agents/.weekly_ack"))
    ack_dir.mkdir(parents=True, exist_ok=True)
    if review_path is None:
        report_dir = Path(getattr(Config, "WEEKLY_REVIEW_REPORT_DIR", "python_agents/reports"))
        review_path = report_dir / f"weekly_strategic_{week_label}.md"
    operator = operator or os.getenv("USER") or os.getenv("USERNAME") or getpass.getuser()
    payload: Dict[str, Any] = {
        "week": week_label,
        "operator": operator,
        "note": str(note or ""),
        "ts": time.time(),
        "ts_iso": datetime.now(timezone.utc).isoformat(),
        "review_path": str(review_path),
        "review_sha256": _sha256_file(review_path),
    }
    out = ack_dir / f".weekly_ack_{week_label}.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Operator weekly acknowledgement.")
    p.add_argument("--week", type=str, required=True, help="ISO week label, e.g. 2026-16")
    p.add_argument("--note", type=str, default="", help="Free-form operator note")
    p.add_argument("--operator", type=str, default=None, help="Override operator identity")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_args(argv)
    out = write_ack(week_label=args.week, note=args.note, operator=args.operator)
    print(json.dumps({"ack_path": str(out), "week": args.week}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
