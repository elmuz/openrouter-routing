from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DEFAULT_F = 0.973
DEFAULT_R = 0.0066
DEFAULT_FW = 0.0


@dataclass(frozen=True)
class EndpointScore:
    slug: str
    provider: str | None
    in_price: float
    out_price: float
    cr_price: float | None
    cw_price: float | None
    cached: bool
    implicit_cache: bool
    f_used: float
    cost: float
    quant: str | None
    ctx: int | None
    status: int | None
    uptime: float | None
    ttft: dict[str, Any] = field(default_factory=dict)
    tps: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Keep the key names used by the original `cheapest.py --json`."""
        return {
            "slug": self.slug,
            "provider": self.provider,
            "in": self.in_price,
            "out": self.out_price,
            "cr": self.cr_price,
            "cw": self.cw_price,
            "cached": self.cached,
            "implicit_cache": self.implicit_cache,
            "f_used": self.f_used,
            "cost": self.cost,
            "quant": self.quant,
            "ctx": self.ctx,
            "status": self.status,
            "uptime": self.uptime,
            "ttft": self.ttft,
            "tps": self.tps,
        }


def price(pricing: dict[str, Any], key: str) -> float | None:
    raw = pricing.get(key)
    if raw in (None, ""):
        return None
    try:
        return float(raw) * 1e6
    except (TypeError, ValueError):
        return None


def score_endpoint(ep: dict[str, Any], f: float, r: float, fw: float) -> EndpointScore:
    pricing = ep.get("pricing") or {}
    p_in = price(pricing, "prompt") or 0.0
    p_out = price(pricing, "completion") or 0.0
    p_cr = price(pricing, "input_cache_read")
    p_cw = price(pricing, "input_cache_write")

    # A provider that cannot cache bills every input token at the input rate,
    # so f (and fw) collapse to 0 for it -- comparing it at the measured f would
    # understate its cost by an order of magnitude.
    cached = p_cr is not None
    eff_f = f if cached else 0.0
    eff_fw = fw if (cached and p_cw is not None) else 0.0
    eff_cr = p_cr if cached else p_in

    return EndpointScore(
        slug=ep.get("tag") or ep.get("provider_slug") or "",
        provider=ep.get("provider_name"),
        in_price=p_in,
        out_price=p_out,
        cr_price=p_cr,
        cw_price=p_cw,
        cached=cached,
        implicit_cache=bool(ep.get("supports_implicit_caching")),
        f_used=eff_f,
        cost=(1 - eff_f) * p_in + eff_f * eff_cr + r * p_out + eff_fw * (p_cw or 0.0),
        quant=ep.get("quantization"),
        ctx=ep.get("context_length"),
        status=ep.get("status"),
        uptime=ep.get("uptime_last_1d"),
        # PercentileStats {p50,p75,p90,p99}; null unless authenticated.
        # Latency (time to first token) is in ms, throughput in tokens/sec.
        ttft=ep.get("latency_last_30m") or {},
        tps=ep.get("throughput_last_30m") or {},
    )


def estimated_cost(mix: dict[str, Any], endpoint: EndpointScore) -> float:
    """USD cost of a mix's token counters on one endpoint (prices are per 1M)."""
    n_in = int(mix.get("input") or 0)
    n_out = int(mix.get("output") or 0)
    n_cr = int(mix.get("cacheRead") or 0)
    n_cw = int(mix.get("cacheWrite") or 0)
    if endpoint.cached:
        p_cr = endpoint.cr_price or 0.0
        p_cw = endpoint.cw_price or 0.0
    else:
        # No cache: every cache-read token is billed as a fresh input token and
        # cache writes cannot happen on that endpoint.
        p_cr = endpoint.in_price
        p_cw = 0.0
    return (n_in * endpoint.in_price + n_cr * p_cr + n_out * endpoint.out_price + n_cw * p_cw) / 1e6


def pct_stat(stat: dict[str, Any] | None, percentile: str) -> float | None:
    value = (stat or {}).get(percentile)
    return float(value) if isinstance(value, (int, float)) else None


def fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.0f}"
