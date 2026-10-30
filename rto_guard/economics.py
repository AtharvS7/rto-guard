from __future__ import annotations
from typing import Any


def rto_cost_per_order(economics: dict[str, Any]) -> float:
    return (
        float(economics["forward_shipping_inr"])
        + float(economics["return_shipping_inr"])
        + float(economics["packaging_handling_inr"])
        + float(economics["damaged_return_rate"]) * float(economics["product_cost_inr"])
    )


def delivered_gross_contribution(economics: dict[str, Any]) -> float:
    return float(economics["assignment_aov_inr"]) * float(economics["delivered_gross_margin"])


def estimate_order_impact(
    risk: float,
    bucket: str,
    economics: dict[str, Any],
) -> dict[str, float]:
    """Expected per-order impact versus shipping every COD order unchanged.

    Assumptions are scenario inputs, not measured causal effects. Hold/cancel
    economics assume that the reviewed hold is ultimately cancelled.
    """
    p = min(max(float(risk), 0.0), 1.0)
    rto_cost = rto_cost_per_order(economics)
    margin = delivered_gross_contribution(economics)
    saved = lost = discount_cost = 0.0

    if bucket == "Ship after confirmation":
        saved = p * float(economics["confirmation_rto_prevention_assumption"]) * rto_cost
        lost = (1 - p) * float(economics["confirmation_genuine_timeout_assumption"]) * margin
        lost += float(economics.get("confirmation_cost_per_order_inr", 0))
    elif bucket == "Nudge to prepaid":
        conversion = float(economics["prepaid_conversion_assumption"])
        prepaid_rto = float(economics["prepaid_rto_rate"])
        discount = float(economics["prepaid_discount_inr"])
        saved = conversion * max(p - prepaid_rto, 0.0) * rto_cost
        discount_cost = conversion * discount
        discount_cost += conversion * float(economics.get("payment_gateway_fee_assumption_inr", 0))
    elif bucket == "Hold / cancel":
        saved = p * rto_cost
        lost = (1 - p) * margin

    net = saved - lost - discount_cost
    return {
        "rto_cost_avoided_inr": round(saved, 2),
        "genuine_margin_lost_inr": round(lost, 2),
        "prepaid_discount_cost_inr": round(discount_cost, 2),
        "net_impact_inr": round(net, 2),
    }


def prepaid_discount_math(economics: dict[str, Any]) -> dict[str, float]:
    cod_rto = 0.28
    prepaid_rto = float(economics["prepaid_rto_rate"])
    rto_cost = rto_cost_per_order(economics)
    discount = float(economics["prepaid_discount_inr"])
    avoided_rto_cost = (cod_rto - prepaid_rto) * rto_cost
    return {
        "rto_cost_per_order": round(rto_cost, 2),
        "expected_cost_saved_per_prepaid_conversion": round(avoided_rto_cost, 2),
        "discount": round(discount, 2),
        "net_before_gateway_fee": round(avoided_rto_cost - discount, 2),
        "break_even_cod_rto_rate": round(prepaid_rto + discount / rto_cost, 4),
        "discount_pct_of_assignment_aov": round(discount / float(economics["assignment_aov_inr"]) * 100, 2),
    }
