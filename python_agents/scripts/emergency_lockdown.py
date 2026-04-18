"""
Aşama 3 — Emergency Lockdown CLI

Usage::

    python python_agents/scripts/emergency_lockdown.py --reason "manual halt"
    python python_agents/scripts/emergency_lockdown.py --release --operator alice
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import List, Optional

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

logger = logging.getLogger("emergency_lockdown_cli")


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Engage / release Aşama 3 emergency lockdown.")
    p.add_argument("--reason", type=str, default="", help="Reason for engaging lockdown")
    p.add_argument("--release", action="store_true", help="Release a previously engaged lockdown")
    p.add_argument("--operator", type=str, default=os.getenv("USER") or "operator")
    p.add_argument("--note", type=str, default="")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_args(argv)
    from emergency_lockdown import get_emergency_lockdown
    lock = get_emergency_lockdown()
    if args.release:
        out = lock.disengage(operator=args.operator, note=args.note)
    else:
        if not args.reason:
            print("error: --reason is required to engage lockdown", file=sys.stderr)
            return 2
        out = lock.engage(reason=args.reason, source="cli", extra={"operator": args.operator})
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
