from __future__ import annotations

CONFIRM_EN = (
    "Hi {name}! Your order #{order_id} for Rs {amount} is ready. Please confirm that "
    "you will be available to receive it. Reply YES to confirm or NO to cancel."
)
CONFIRM_HI = (
    "Namaste {name}! Aapka Rs {amount} ka order #{order_id} dispatch ke liye ready hai. "
    "Kya aap delivery receive kar paayenge? Confirm karne ke liye YES, cancel ke liye NO reply karein."
)
PREPAID_EN = (
    "Hi {name}! Pay online for order #{order_id} and get Rs {discount} off (total Rs {total}). "
    "Reply YES to receive the secure checkout link, or NO to continue with COD. Offer valid for {validity_hours} hours."
)
PREPAID_HI = (
    "Namaste {name}! Order #{order_id} par online payment karne par Rs {discount} ki chhoot milegi. "
    "Checkout link ke liye YES, COD par continue karne ke liye NO reply karein. Offer {validity_hours} ghante valid hai."
)


def initial_message(action: str, order: dict, validity_hours: int = 24) -> dict[str, str]:
    name = order.get("customer_name") or "Customer"
    order_id = order.get("order_id") or "DEMO"
    amount = int(float(order.get("order_value", 649)))
    discount = int(order.get("discount", 25))
    total = max(amount - discount, 0)
    if action == "Ship after confirmation":
        return {"english": CONFIRM_EN.format(name=name, order_id=order_id, amount=amount),
                "hinglish": CONFIRM_HI.format(name=name, order_id=order_id, amount=amount)}
    if action == "Nudge to prepaid":
        return {
            "english": PREPAID_EN.format(name=name, order_id=order_id, discount=discount, total=total, validity_hours=validity_hours),
            "hinglish": PREPAID_HI.format(name=name, order_id=order_id, discount=discount, validity_hours=validity_hours),
        }
    raise ValueError("WhatsApp messages are only defined for confirmation and prepaid nudge actions")


def reminder_message(
    action: str,
    order_id: str = "DEMO",
    discount: int = 25,
    validity_hours: int = 24,
) -> str:
    if action == "Ship after confirmation":
        return (
            f"Reminder: please reply YES or NO for order #{order_id}. We will not dispatch without confirmation. "
            f"/ Yaad-dihani: order #{order_id} receive karne ke liye YES ya cancel karne ke liye NO reply karein."
        )
    return (
        f"Reminder: your Rs {discount} prepaid offer for order #{order_id} is still available until "
        f"{validity_hours} hours after the original offer was sent. Reply YES for checkout or NO to keep COD. "
        f"/ Yaad-dihani: Rs {discount} ki chhoot original offer bhejne ke {validity_hours} ghante tak valid hai. "
        "Checkout link ke liye YES ya COD ke liye NO reply karein."
    )


def process_reply(action: str, reply: str, status: str = "pending") -> dict[str, str]:
    token = " ".join((reply or "").strip().lower().split())
    if action == "Ship after confirmation":
        if token in {"yes", "y", "haan", "ha", "confirm", "confirmed"}:
            return {"status": "confirmed_to_ship", "message": "Thanks! Your order is confirmed and will be handed to the courier."}
        if token in {"no", "n", "nahi", "nahin", "cancel", "cancel it"}:
            return {"status": "customer_cancelled", "message": "Your COD order has been cancelled as requested. You can place a new order any time."}
        if status == "needs_clarification":
            return {"status": "manual_agent_review", "message": "We still could not confirm your reply. A support agent will review this order before dispatch."}
        return {"status": "needs_clarification", "message": "Sorry, I did not understand. Please reply only YES to confirm or NO to cancel."}

    if action == "Nudge to prepaid":
        cod_states = {"cod_confirmation_required", "cod_confirmation_needs_clarification", "offer_expired_cod_reconfirmation"}
        if status in cod_states:
            if token in {"yes", "y", "haan", "ha", "confirm", "confirmed"}:
                return {"status": "confirmed_to_ship_cod", "message": "Thanks! Your COD order is confirmed and will be handed to the courier."}
            if token in {"no", "n", "nahi", "nahin", "cancel"}:
                return {"status": "customer_cancelled", "message": "Your COD order has been cancelled as requested. You can place a new order any time."}
            if status == "cod_confirmation_needs_clarification":
                return {"status": "manual_agent_review", "message": "We still could not confirm your reply. A support agent will contact you before dispatch."}
            return {"status": "cod_confirmation_needs_clarification", "message": "Please reply YES to confirm COD delivery or NO to cancel."}
        if token in {"yes", "y", "haan", "ha", "link", "pay"}:
            return {"status": "payment_link_sent", "message": "Mock checkout link created. Order will switch to prepaid only after payment is confirmed."}
        if token in {"paid", "payment done", "done", "success"} and status == "payment_link_sent":
            return {"status": "prepaid_payment_confirmed", "message": "Payment confirmed in the simulator. Order moved to prepaid fulfilment."}
        if token in {"no", "n", "nahi", "nahin", "cod"}:
            return {"status": "cod_confirmation_required", "message": "No problem. We will keep COD. Please reply YES to confirm you can receive the parcel."}
        if status == "needs_clarification":
            return {"status": "manual_agent_review", "message": "We still could not understand your reply. A support agent will review the order."}
        return {"status": "needs_clarification", "message": "Please reply YES for a prepaid link or NO to continue with COD."}

    raise ValueError("Unknown WhatsApp action")


def timeout_result(action: str, deadline_hours: int = 24) -> dict[str, str]:
    if action == "Ship after confirmation":
        return {
            "status": "cancelled_no_confirmation",
            "message": f"No confirmation was received within {deadline_hours} hours. The order is not dispatched and is marked cancelled. The customer can reorder.",
        }
    return {
        "status": "offer_expired_cod_reconfirmation",
        "message": f"The prepaid offer expired after {deadline_hours} hours. Keep the original COD order; send one confirmation request or route it to an agent. Do not cancel only because the offer was ignored.",
    }
