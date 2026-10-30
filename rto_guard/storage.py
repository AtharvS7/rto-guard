from __future__ import annotations
from pathlib import Path
import sqlite3
from typing import Any
import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'live',
    bucket TEXT NOT NULL,
    operational_action TEXT NOT NULL,
    risk_score REAL NOT NULL,
    order_value REAL NOT NULL,
    city TEXT,
    state TEXT,
    rto_cost_avoided_inr REAL NOT NULL,
    genuine_margin_lost_inr REAL NOT NULL,
    prepaid_discount_cost_inr REAL NOT NULL,
    net_impact_inr REAL NOT NULL,
    reason TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decisions_created_at ON decisions(created_at);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    return connection


def log_decision(db_path: str | Path, order: dict[str, Any], result: dict[str, Any], created_at: str, mode: str = "live") -> None:
    with connect(db_path) as conn:
        conn.execute(
            """INSERT INTO decisions (order_id, created_at, mode, bucket, operational_action,
               risk_score, order_value, city, state, rto_cost_avoided_inr,
               genuine_margin_lost_inr, prepaid_discount_cost_inr, net_impact_inr, reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(order.get("order_id") or "ORDER"), created_at, mode, result["bucket"],
                result["operational_action"], float(result["risk_score"]),
                float(order.get("order_value", 0)), str(order.get("city") or "Unknown"),
                str(order.get("state") or "Unknown"), float(result["rto_cost_avoided_inr"]),
                float(result["genuine_margin_lost_inr"]), float(result["prepaid_discount_cost_inr"]),
                float(result["net_impact_inr"]), result["reason"],
            ),
        )


def read_decisions(db_path: str | Path) -> pd.DataFrame:
    with connect(db_path) as conn:
        return pd.read_sql_query("SELECT * FROM decisions ORDER BY created_at DESC", conn)


def seed_demo_snapshot(db_path: str | Path, csv_path: str | Path, created_at: str) -> int:
    frame = pd.read_csv(csv_path)
    rows = []
    for idx, row in frame.iterrows():
        rows.append((
            f"DEMO-{idx + 1:04d}", created_at, "demo", row["bucket"],
            "Hold for manual review" if row["bucket"] == "Hold / cancel" else row["bucket"],
            float(row["risk_score"]), float(row["order_value"]), str(row["city"]), str(row["state"]),
            float(row["rto_cost_avoided_inr"]), float(row["genuine_margin_lost_inr"]),
            float(row["prepaid_discount_cost_inr"]), float(row["net_impact_inr"]),
            "Demo replay from the September temporal holdout; not a live order decision.",
        ))
    with connect(db_path) as conn:
        conn.execute("DELETE FROM decisions WHERE mode = 'demo'")
        conn.executemany(
            """INSERT INTO decisions (order_id, created_at, mode, bucket, operational_action,
               risk_score, order_value, city, state, rto_cost_avoided_inr,
               genuine_margin_lost_inr, prepaid_discount_cost_inr, net_impact_inr, reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", rows
        )
    return len(rows)
