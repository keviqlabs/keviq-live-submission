"""Model API prices (USD), from dev.meta.ai/docs/pricing-rate-limits, read 2026-10-27, Standard mode."""

import math

PRICES = {
    "muse-spark-1.3": {"input_per_m": 1.25, "cached_input_per_m": 0.15, "output_per_m": 4.25},
    "sam-3.1": {"per_image": 2.50 / 1000, "per_frame": 0.20 / 1000, "min_frames_per_video": 50},
    "muse-voice-transcribe-1.0": {"per_hour": 0.18},
    "muse-image-1.0": {"per_image": 0.01},
}
BLOCKED = {"muse-wonder-1.0": "Muse Wonder is not used this week (budget rule)"}


class UnknownPrice(KeyError):
    pass


def cost_usd(
    model: str, tokens_in: int = 0, tokens_out: int = 0, cached_in: int = 0, units: dict | None = None
) -> float:
    """Cost of one call. Unknown models raise: a call we cannot price is a call we cannot cap."""
    price = PRICES.get(model)
    if price is None:
        raise UnknownPrice(f"no price for model {model!r}; add it to meta/pricing.py from the docs")
    units = units or {}
    if "input_per_m" in price:
        uncached = max(tokens_in - cached_in, 0)
        return (
            uncached * price["input_per_m"]
            + cached_in * price["cached_input_per_m"]
            + tokens_out * price["output_per_m"]
        ) / 1_000_000
    if "per_hour" in price:
        return math.floor(units.get("audio_seconds", 0)) * price["per_hour"] / 3600
    total = units.get("images", 0) * price.get("per_image", 0)
    if "per_frame" in price and units.get("frames"):
        total += max(units["frames"], price["min_frames_per_video"]) * price["per_frame"]
    return total
