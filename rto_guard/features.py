from __future__ import annotations

import re
from typing import Any
import numpy as np
import pandas as pd

CATEGORICAL_FEATURES = ["city", "state", "courier", "utm_source", "item_family"]
NUMERIC_FEATURES = [
    "order_value", "num_items", "customer_prior_orders", "customer_prior_rto",
    "prior_rto_rate", "has_customer_history", "hour", "is_weekend",
    "address_len", "address_has_digit", "address_has_landmark",
    "address_has_locality", "address_is_vague", "name_is_suspicious",
    "phone_valid", "pincode_missing", "city_missing",
]
FEATURE_COLUMNS = NUMERIC_FEATURES + CATEGORICAL_FEATURES

LANDMARK_RE = re.compile(
    r"\b(near|opp|opposite|saamne|paas|pass|ke paas|mandir|school|bank|station|market|chowk|landmark)\b",
    re.IGNORECASE,
)
LOCALITY_RE = re.compile(
    r"\b(sector|nagar|society|colony|road|marg|gali|street|phase|block|apartment|flat|building)\b",
    re.IGNORECASE,
)
SUSPICIOUS_NAME_RE = re.compile(
    r"(?:^|\s)(?:asdfg?|qwerty|test|dummy|unknown)(?:\s|$)|\d{3,}", re.IGNORECASE
)


def _safe_text(value: Any) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ""
    return str(value).strip()


def item_family(value: Any) -> str:
    """Compact, privacy-safe token representation of the product list."""
    raw = _safe_text(value).lower()
    tokens = sorted(set(re.findall(r"[a-z]+", raw)))
    return "|".join(tokens)[:100] or "unknown"


def engineer_features(orders: pd.DataFrame) -> pd.DataFrame:
    """Build the exact feature contract used in training and inference.

    Raw customer name, phone, full address, order ID and exact pincode are not
    passed to the classifier. Only coarse derived quality flags are retained.
    """
    required = {
        "order_datetime", "customer_name", "phone", "address", "city", "state",
        "order_value", "items", "num_items", "customer_prior_orders",
        "customer_prior_rto", "utm_source", "courier", "pincode",
    }
    missing = required - set(orders.columns)
    if missing:
        raise ValueError(f"Missing required order fields: {', '.join(sorted(missing))}")

    df = orders.copy()
    out = pd.DataFrame(index=df.index)
    order_value = pd.to_numeric(df["order_value"], errors="coerce").astype(float)
    # Zero/negative basket values are treated as missing rather than as a
    # genuine low-value signal; the sample contains a few malformed rows.
    out["order_value"] = order_value.where(order_value > 0, np.nan)
    for col in ["num_items", "customer_prior_orders", "customer_prior_rto"]:
        out[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(float)

    prior_orders = out["customer_prior_orders"].replace(0, np.nan)
    out["prior_rto_rate"] = (out["customer_prior_rto"] / prior_orders).clip(0, 1)
    out["has_customer_history"] = (out["customer_prior_orders"] > 0).astype(int)

    order_dt = pd.to_datetime(df["order_datetime"], errors="coerce")
    out["hour"] = order_dt.dt.hour.fillna(12).astype(int)
    out["is_weekend"] = (order_dt.dt.dayofweek.fillna(0) >= 5).astype(int)

    for col in ["city", "state", "courier", "utm_source"]:
        out[col] = df[col].fillna("Unknown").astype(str).str.strip().replace("", "Unknown")
    out["item_family"] = df["items"].map(item_family)

    address = df["address"].map(_safe_text).str.lower()
    name = df["customer_name"].map(_safe_text).str.lower()
    phone = df["phone"].map(_safe_text).str.replace(r"\D", "", regex=True)

    out["address_len"] = address.str.len().astype(float)
    out["address_has_digit"] = address.str.contains(r"\d", regex=True).astype(int)
    out["address_has_landmark"] = address.map(lambda value: int(bool(LANDMARK_RE.search(value))))
    out["address_has_locality"] = address.map(lambda value: int(bool(LOCALITY_RE.search(value))))
    vague_values = {"", "home", "address", "near station", "near market", "near temple"}
    out["address_is_vague"] = address.str.strip().isin(vague_values).astype(int)
    out["name_is_suspicious"] = name.map(lambda value: int(bool(SUSPICIOUS_NAME_RE.search(value))))
    out["phone_valid"] = phone.str.len().eq(10).astype(int)
    out["pincode_missing"] = df["pincode"].isna().astype(int)
    out["city_missing"] = df["city"].isna().astype(int)

    # CatBoost expects categorical values as strings; ensure stable column order.
    for col in CATEGORICAL_FEATURES:
        out[col] = out[col].fillna("Unknown").astype(str)
    return out[FEATURE_COLUMNS]
