from __future__ import annotations
from pathlib import Path
from catboost import CatBoostClassifier


def load_model(path: str | Path) -> CatBoostClassifier:
    model = CatBoostClassifier()
    model.load_model(str(path))
    return model
