"""Order state machine, loaded from contracts/order_states.json (the contract is the source of truth)."""

import json
from functools import cache

from app.settings import ROOT


@cache
def machine() -> dict:
    return json.loads((ROOT / "contracts" / "order_states.json").read_text())


def can_transition(
    from_state: str | None, to_state: str, actor: str, customer_confirmed: bool = False
) -> tuple[bool, str]:
    """Return (allowed, reason). `customer_confirmed` = the customer confirmed this order at some point before."""
    m = machine()
    if to_state not in m["states"]:
        return False, f"unknown state {to_state}"
    if from_state is None:
        return (
            to_state == m["initial"],
            "new orders start in DRAFT" if to_state != m["initial"] else "create",
        )
    if from_state in m["terminal"]:
        return False, f"{from_state} is terminal"
    if to_state not in m["transitions"][from_state]:
        return False, f"{from_state} -> {to_state} is not a transition"
    if (
        from_state == "NEEDS_SELLER"
        and actor != "seller"
        and not (to_state == "CANCELLED" and actor == "customer")
    ):
        return False, "only the seller leaves NEEDS_SELLER (the customer may cancel)"
    if to_state == "CONFIRMED":
        if from_state == "CONFIRM_SENT" and actor != "customer":
            return False, "CONFIRMED needs the customer's own confirmation"
        if from_state == "NEEDS_SELLER" and not customer_confirmed:
            return False, "the customer has not confirmed this order yet"
    return True, "ok"
