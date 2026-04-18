"""
build_signature_library.py — Path signature eğitim kütüphanesi (§5 yardımcısı)
==============================================================================
`counterfactual_observations` tablosundan label='TP' kayıtlarını çekerek,
t-30m → t-10m penceresinde path signature hesapla ve ChromaDB
`whale_execution_signatures` koleksiyonuna yaz. Scaffold — PR2'de genişler.

Kullanım:
    python scripts/build_signature_library.py --dry-run
    python scripts/build_signature_library.py --limit 100
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# python_agents root'u sys.path'e al
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

logger = logging.getLogger("build_signature_library")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Yazma yapma, sayım gör")
    parser.add_argument("--limit", type=int, default=0, help="Maks kayıt sayısı (0=sınırsız)")
    parser.add_argument("--collection", default="whale_execution_signatures")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logger.info("build_signature_library scaffold — dry_run=%s limit=%d", args.dry_run, args.limit)

    try:
        from database import Database
        from path_signature_engine import get_path_signature
    except Exception as e:
        logger.error("Import başarısız: %s", e)
        return 2

    # PR2'de doldurulacak: counterfactual_observations sorgusu + signature hesabı + chroma yazımı
    logger.info("⏳ Scaffold: kayıt tarama ve signature hesabı PR2'de etkinleşecek.")
    logger.info("Target collection: %s", args.collection)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
