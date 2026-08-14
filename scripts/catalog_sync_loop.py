#!/usr/bin/env python3
"""Run OTOBO catalog sync in a loop (Docker sidecar)."""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path

from app.core.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("catalog-sync")

SYNC_SCRIPT = Path(__file__).resolve().with_name("sync_otobo_catalog.py")


def run() -> None:
    while True:
        from app.core.config import invalidate_settings_cache

        invalidate_settings_cache()
        settings = get_settings()
        interval = max(10, int(settings.catalog_sync_interval_seconds))
        log.info("Catalog sync every %s seconds", interval)
        try:
            result = subprocess.run(
                [sys.executable, str(SYNC_SCRIPT)],
                check=False,
            )
            log.info("Sync finished rc=%s", result.returncode)
        except Exception:
            log.exception("Catalog sync failed")
        time.sleep(interval)


if __name__ == "__main__":
    run()
