"""Load config.yaml (+ .env secrets). Paths are resolved relative to the repo root."""
from __future__ import annotations

import os
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@lru_cache(maxsize=1)
def load_config(path: str | os.PathLike | None = None) -> dict[str, Any]:
    load_dotenv(ROOT / ".env")
    cfg_path = Path(path) if path else Path(os.environ.get("RNS_CONFIG", ROOT / "config.yaml"))
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)
    for k, v in cfg.get("paths", {}).items():
        p = Path(v)
        cfg["paths"][k] = p if p.is_absolute() else ROOT / p
    for p in cfg["paths"].values():
        (p.parent if p.suffix else p).mkdir(parents=True, exist_ok=True)
    return cfg


def date_range(cfg: dict) -> tuple[date, date]:
    d = cfg["dates"]
    start = d["start"] if isinstance(d["start"], date) else date.fromisoformat(str(d["start"]))
    end = d.get("end") or date.today()
    if not isinstance(end, date):
        end = date.fromisoformat(str(end))
    return start, end
