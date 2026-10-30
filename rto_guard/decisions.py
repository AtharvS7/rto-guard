from __future__ import annotations
from typing import Any
import pandas as pd
from .features import engineer_features
from .economics import estimate_order_impact

BUCKETS = ["Ship as-is", "Ship after confirmation", "Nudge to prepaid", "Hold / cancel"]


def bucket_for_risk(risk: float, thresholds: dict[str, float]) -> str:
    score = float(risk)
    if score < float(thresholds["ship_as_is_below"]):
        return "Ship as-is"
    if score < float(thresholds["confirm_below"]):
        return "Ship after confirmation"
    if score < float(thresholds["nudge_below"]):
        return "Nudge to prepaid"
    return "Hold / cancel"


def _reason_for(bucket: str, risk: float, features: pd.Series, llm_assessment: dict[str, Any] | None) -> str:
    reasons = [f"The model estimates {risk:.1%} RTO risk for this order."]
    if features.get("customer_prior_rto", 0) >= 1:
        reasons.append(f"This customer has {int(features['customer_prior_rto'])} previous RTO(s).")
    if features.get("address_is_vague", 0) or features.get("address_len", 0) < 18:
        reasons.append("The address is short or vague and needs a delivery check.")
    elif not features.get("address_has_digit", 0) and not features.get("address_has_locality", 0):
        reasons.append("The address has limited house/locality detail.")
    if features.get("name_is_suspicious", 0):
        reasons.append("The customer name needs a quick manual check.")
    if llm_assessment:
        if not llm_assessment.get("address_usable", True):
            reasons.append("AI address review found the address ambiguous or unusable; review it manually.")
        elif llm_assessment.get("address_completeness") == "partial":
            reasons.append("AI address review found missing delivery details.")
        if not llm_assessment.get("name_plausible", True):
            reasons.append("AI address review flagged the name for a human check.")
    if bucket == "Ship as-is":
        reasons.append("No intervention is justified by the configured risk thresholds.")
    elif bucket == "Ship after confirmation":
        reasons.append("A lightweight WhatsApp confirmation is proportionate at this risk level.")
    elif bucket == "Nudge to prepaid":
        reasons.append("A prepaid offer is cheaper than the expected COD RTO exposure at this risk level.")
    else:
        reasons.append("Do not dispatch yet; send to an operations reviewer before any cancellation.")
    return " ".join(reasons)


def make_decision(
    order: dict[str, Any],
    model: Any,
    config: dict[str, Any],
    llm_assessment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    frame = pd.DataFrame([order])
    features = engineer_features(frame)
    risk = float(model.predict_proba(features)[0][1])
    thresholds = config["risk_thresholds"]
    bucket = bucket_for_risk(risk, thresholds)

    # LLM is a narrow quality assistant, not the risk model. A weak/ambiguous
    # address can only escalate a shipment to a human review/confirmation.
    if llm_assessment:
        complete = llm_assessment.get("address_completeness", "complete")
        if not llm_assessment.get("address_usable", True):
            bucket = "Hold / cancel"
        elif (complete == "partial" or not llm_assessment.get("name_plausible", True)) and bucket == "Ship as-is":
            bucket = "Ship after confirmation"

    # Deterministic input quality guard: never silently dispatch a bare address.
    if (features.iloc[0]["address_is_vague"] or features.iloc[0]["name_is_suspicious"]) and bucket == "Ship as-is":
        bucket = "Ship after confirmation"

    first_time = int(order.get("customer_prior_orders", 0) or 0) == 0
    auto_cancel_enabled = bool(config["policy"].get("auto_cancel_enabled", False))
    first_time_protection = bool(config["policy"].get("first_time_customer_protection", True))
    prior_rto_min = int(config["policy"].get("auto_cancel_min_prior_rto", 2))
    can_auto_cancel = (
        bucket == "Hold / cancel"
        and auto_cancel_enabled
        and int(order.get("customer_prior_rto", 0) or 0) >= prior_rto_min
        and not (first_time and first_time_protection)
    )
    operational_action = "Cancel after human review" if can_auto_cancel else (
        "Hold for manual review" if bucket == "Hold / cancel" else bucket
    )

    reason = _reason_for(bucket, risk, features.iloc[0], llm_assessment)
    impact = estimate_order_impact(risk, bucket, config["unit_economics"])
    return {
        "risk_score": round(risk, 4),
        "risk_percent": round(risk * 100, 1),
        "bucket": bucket,
        "operational_action": operational_action,
        "reason": reason,
        "first_time_customer": first_time,
        "auto_cancelled": False,
        "llm_review_used": bool(llm_assessment),
        "llm_assessment": llm_assessment,
        **impact,
    }
