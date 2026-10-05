"""INR formatting for the dashboard's presentation-only cost values."""

import math


INR_PER_COST_UNIT = 85.0


def inr_to_cost_units(amount_inr: float) -> float:
    if not math.isfinite(amount_inr):
        raise ValueError("currency amount must be finite")
    return amount_inr / INR_PER_COST_UNIT


def cost_units_to_inr(amount: float) -> float:
    if not math.isfinite(amount):
        raise ValueError("currency amount must be finite")
    return amount * INR_PER_COST_UNIT


def format_inr(amount_inr: float) -> str:
    if not math.isfinite(amount_inr):
        raise ValueError("currency amount must be finite")
    return f"₹{amount_inr:,.2f}"


def format_cost(amount: float) -> str:
    return format_inr(cost_units_to_inr(amount))
