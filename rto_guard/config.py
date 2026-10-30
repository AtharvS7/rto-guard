from __future__ import annotations

from pathlib import Path
from typing import Any
import os
import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    config_path = Path(path) if path else ROOT / "config.yaml"
    with config_path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    db_override = os.getenv("RTO_DB_PATH")
    if db_override:
        config.setdefault("app", {})["database_path"] = db_override
    return config


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path
