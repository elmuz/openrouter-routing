from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .api import OpenRouterClient
from .errors import ConfigError, OpenRouterError, OpenRouterNotFound
from .mix import apply_window, filter_records, resolve_window, validate_project_roots
from .pricing import EndpointScore, estimated_cost, price, score_endpoint
from .sessions import SessionRecord

DEFAULT_LAST = 20
DEFAULT_TOLERANCE = 0.05
_OLDEST = datetime.min.replace(tzinfo=UTC)


@dataclass(frozen=True)
class GenerationCheck:
    generation_id: str
    record: SessionRecord
    status: str
    provider: str | None = None
    model: str | None = None
    endpoint: str | None = None
    predicted_cost: float | None = None
    actual_cost: float | None = None
    delta_usd: float | None = None
    delta_pct: float | None = None
    cached_tokens: int | None = None
    cache_discount: float | None = None
    flags: tuple[str, ...] = ()
    error: str | None = None

    @property
    def total_input(self) -> int:
        return self.record.input_tokens + self.record.cache_read

    def as_dict(self) -> dict[str, Any]:
        return {
            "generation_id": self.generation_id,
            "timestamp": self.record.timestamp.isoformat() if self.record.timestamp else None,
            "session_id": self.record.session_id,
            "project": self.record.cwd,
            "requested_model": self.record.model,
            "routed_model": self.record.routed_model,
            "status": self.status,
            "provider": self.provider,
            "generation_model": self.model,
            "endpoint": self.endpoint,
            "input": self.record.input_tokens,
            "cacheRead": self.record.cache_read,
            "cacheWrite": self.record.cache_write,
            "output": self.record.output_tokens,
            "total_input": self.total_input,
            "predicted_cost_usd": self.predicted_cost,
            "actual_cost_usd": self.actual_cost,
            "delta_usd": self.delta_usd,
            "delta_pct": self.delta_pct,
            "generation_cached_tokens": self.cached_tokens,
            "cache_discount_usd": self.cache_discount,
            "flags": list(self.flags),
            "error": self.error,
        }


@dataclass(frozen=True)
class VerifySummary:
    selected: int = 0
    verified: int = 0
    missing: int = 0
    unmatched: int = 0
    errors: int = 0
    mismatches: int = 0
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    total_input: int = 0
    predicted_cost: float = 0.0
    actual_cost: float = 0.0
    cache_discount: float = 0.0
    f: float = 0.0
    R: float = 0.0
    fw: float = 0.0
    predicted_c_eff: float | None = None
    actual_c_eff: float | None = None
    delta_usd: float = 0.0
    delta_pct: float | None = None
    providers: dict[str, int] = field(default_factory=dict)
    flags: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "selected": self.selected,
            "verified": self.verified,
            "missing": self.missing,
            "unmatched": self.unmatched,
            "errors": self.errors,
            "mismatches": self.mismatches,
            "input": self.input,
            "output": self.output,
            "cacheRead": self.cache_read,
            "cacheWrite": self.cache_write,
            "total_input": self.total_input,
            "f": self.f,
            "R": self.R,
            "fw": self.fw,
            "predicted_cost_usd": self.predicted_cost,
            "actual_cost_usd": self.actual_cost,
            "cache_discount_usd": self.cache_discount,
            "predicted_c_eff": self.predicted_c_eff,
            "actual_c_eff": self.actual_c_eff,
            "delta_usd": self.delta_usd,
            "delta_pct": self.delta_pct,
            "providers": dict(self.providers),
            "flags": dict(self.flags),
        }


@dataclass(frozen=True)
class VerifyResult:
    checks: tuple[GenerationCheck, ...]
    summary: VerifySummary
    filters: dict[str, Any] = field(default_factory=dict)


def _norm(value: str | None) -> str:
    return "".join(character for character in (value or "").lower() if character.isalnum())


def _sort_key(record: SessionRecord) -> tuple[datetime, str]:
    return (record.timestamp or _OLDEST, record.response_id or "")


def select_verifiable(
    records: Iterable[SessionRecord],
    *,
    last: int | None = None,
    since: str | None = None,
    until: str | None = None,
    models: Sequence[str] = (),
    routed_models: Sequence[str] = (),
    exclude_models: Sequence[str] = (),
    providers: Sequence[str] = (),
    projects: Sequence[str] = (),
    exclude_projects: Sequence[str] = (),
) -> list[SessionRecord]:
    """Select records with a responseId, newest first."""
    rows = [record for record in records if record.response_id]
    validate_project_roots([*projects, *exclude_projects])
    rows = filter_records(
        rows,
        models=models,
        routed_models=routed_models,
        exclude_models=exclude_models,
        providers=providers,
        projects=projects,
        exclude_projects=exclude_projects,
    )
    if since is not None or until is not None:
        window = resolve_window(rows, since=since, until=until)
        rows = apply_window(rows, window)
    rows.sort(key=_sort_key, reverse=True)
    if last is not None:
        if last < 1:
            raise ConfigError("--last must be >= 1")
        rows = rows[:last]

    seen: set[str] = set()
    unique: list[SessionRecord] = []
    for record in rows:
        key = record.response_id or ""
        if key in seen:
            continue
        seen.add(key)
        unique.append(record)
    return unique


class _EndpointResolver:
    def __init__(self, client: OpenRouterClient) -> None:
        self.client = client
        self._cache: dict[str, list[dict[str, Any]] | OpenRouterError] = {}

    def _endpoints(self, model: str) -> list[dict[str, Any]]:
        cached = self._cache.get(model)
        if cached is None:
            try:
                payload = self.client.fetch_endpoints(model)
                cached = list(payload.get("endpoints") or [])
            except OpenRouterError as exc:
                cached = exc
            self._cache[model] = cached
        if isinstance(cached, OpenRouterError):
            raise cached
        return cached

    def find(self, model: str | None, provider: str | None, cache_read: int) -> EndpointScore | None:
        if not model or not provider:
            return None
        wanted = _norm(provider)
        candidates = [
            endpoint
            for endpoint in self._endpoints(model)
            if _norm(str(endpoint.get("provider_name") or "")) == wanted
        ]
        if not candidates:
            candidates = [
                endpoint
                for endpoint in self._endpoints(model)
                if _norm(str(endpoint.get("tag") or "").split("/")[0]) == wanted
            ]
        if len(candidates) > 1 and cache_read:
            cached = [
                endpoint
                for endpoint in candidates
                if price(endpoint.get("pricing") or {}, "input_cache_read") is not None
            ]
            if cached:
                candidates = cached
        candidates.sort(key=lambda endpoint: (endpoint.get("status") != 0, str(endpoint.get("tag") or "")))
        if not candidates:
            return None
        return score_endpoint(candidates[0], 0.0, 0.0, 0.0)


def _usage_mix(record: SessionRecord) -> dict[str, int]:
    return {
        "input": record.input_tokens,
        "cacheRead": record.cache_read,
        "cacheWrite": record.cache_write,
        "output": record.output_tokens,
    }


def _mismatch(predicted: float, actual: float, tolerance: float) -> bool:
    scale = max(abs(predicted), abs(actual))
    return abs(actual - predicted) > max(1e-7, tolerance * scale)


def _flags(
    record: SessionRecord,
    generation: dict[str, Any],
    endpoint: EndpointScore,
    predicted: float,
    actual: float,
    delta_pct: float | None,
    tolerance: float,
) -> tuple[str, ...]:
    flags: list[str] = []
    if _mismatch(predicted, actual, tolerance):
        flags.append(f"cost mismatch {delta_pct:+.1f}%" if delta_pct is not None else "cost mismatch")

    cached = generation.get("native_tokens_cached")
    if isinstance(cached, int) and cached != record.cache_read:
        if cached < record.cache_read:
            flags.append(
                f"generation cached {cached} vs pi {record.cache_read}: "
                "cache TTL, minimum prompt size, or provider fallback"
            )
        else:
            flags.append(
                f"generation cached {cached} vs pi {record.cache_read}: tokenizer/reporting difference"
            )

    if record.cache_read and endpoint.cr_price is None:
        flags.append("cache reads but endpoint has no cache-read price")
    if record.cache_write:
        if endpoint.cw_price is None:
            flags.append("cache writes but endpoint has no cache-write price")
        elif endpoint.cw_price > endpoint.in_price:
            flags.append("cache-write price exceeds the input price")
    if endpoint.cached and record.cache_read == 0:
        if record.input_tokens < 1024:
            flags.append("no cache reads on a small prompt; may be below the provider minimum")
        else:
            flags.append("no cache reads; cache may have expired or the provider skipped it")
    if record.cache_read and not generation.get("cache_discount"):
        flags.append("cache reads but generation reports no cache discount")

    upstream = generation.get("upstream_inference_cost")
    if generation.get("is_byok") or (
        isinstance(upstream, (int, float)) and bool(upstream) and abs(float(upstream) - actual) > 1e-9
    ):
        flags.append("BYOK/upstream cost differs; total_cost is not directly comparable")

    pi_prompt = record.input_tokens + record.cache_read
    native_prompt = generation.get("native_tokens_prompt")
    if isinstance(native_prompt, (int, float)) and pi_prompt:
        if abs(float(native_prompt) - pi_prompt) > max(32, 0.05 * pi_prompt):
            flags.append(
                f"prompt tokens differ (generation {native_prompt:g} vs pi {pi_prompt}): "
                "truncation or tokenizer normalization"
            )
    native_completion = generation.get("native_tokens_completion")
    if isinstance(native_completion, (int, float)) and record.output_tokens:
        if abs(float(native_completion) - record.output_tokens) > max(8, 0.05 * record.output_tokens):
            flags.append(
                f"completion tokens differ (generation {native_completion:g} vs pi {record.output_tokens})"
            )
    return tuple(flags)


def _check(
    record: SessionRecord,
    *,
    client: OpenRouterClient,
    resolver: _EndpointResolver,
    tolerance: float,
) -> GenerationCheck:
    generation_id = record.response_id or ""
    try:
        generation = client.fetch_generation(generation_id)
    except OpenRouterNotFound:
        return GenerationCheck(
            generation_id=generation_id,
            record=record,
            status="missing",
            error="generation record not found or expired",
        )
    except OpenRouterError as exc:
        return GenerationCheck(
            generation_id=generation_id,
            record=record,
            status="error",
            error=str(exc),
        )

    provider = generation.get("provider_name")
    model = generation.get("model") or record.routed_model or record.model
    try:
        endpoint = resolver.find(
            model if isinstance(model, str) else None,
            provider if isinstance(provider, str) else None,
            record.cache_read,
        )
    except OpenRouterError as exc:
        return GenerationCheck(
            generation_id=generation_id,
            record=record,
            status="error",
            provider=provider if isinstance(provider, str) else None,
            model=model if isinstance(model, str) else None,
            error=str(exc),
        )
    if endpoint is None:
        return GenerationCheck(
            generation_id=generation_id,
            record=record,
            status="unmatched",
            provider=provider if isinstance(provider, str) else None,
            model=model if isinstance(model, str) else None,
            error="provider not found in the model's endpoint list",
        )

    actual_raw = generation.get("total_cost")
    if not isinstance(actual_raw, (int, float)):
        return GenerationCheck(
            generation_id=generation_id,
            record=record,
            status="error",
            provider=provider if isinstance(provider, str) else None,
            model=model if isinstance(model, str) else None,
            endpoint=endpoint.slug,
            error="generation has no total_cost",
        )
    actual = float(actual_raw)
    predicted = estimated_cost(_usage_mix(record), endpoint)
    delta = actual - predicted
    scale = max(abs(predicted), abs(actual), 1e-12)
    delta_pct = delta / scale * 100
    flags = _flags(record, generation, endpoint, predicted, actual, delta_pct, tolerance)
    cached = generation.get("native_tokens_cached")
    discount = generation.get("cache_discount")
    return GenerationCheck(
        generation_id=generation_id,
        record=record,
        status="mismatch" if _mismatch(predicted, actual, tolerance) else "ok",
        provider=provider if isinstance(provider, str) else None,
        model=model if isinstance(model, str) else None,
        endpoint=endpoint.slug,
        predicted_cost=predicted,
        actual_cost=actual,
        delta_usd=delta,
        delta_pct=delta_pct,
        cached_tokens=cached if isinstance(cached, int) else None,
        cache_discount=float(discount) if isinstance(discount, (int, float)) else None,
        flags=flags,
    )


def _summary(checks: Sequence[GenerationCheck]) -> VerifySummary:
    verified = [check for check in checks if check.status in {"ok", "mismatch"}]
    input_tokens = sum(check.record.input_tokens for check in verified)
    output_tokens = sum(check.record.output_tokens for check in verified)
    cache_read = sum(check.record.cache_read for check in verified)
    cache_write = sum(check.record.cache_write for check in verified)
    total_input = input_tokens + cache_read
    predicted = sum(check.predicted_cost or 0.0 for check in verified)
    actual = sum(check.actual_cost or 0.0 for check in verified)
    discount = sum(check.cache_discount or 0.0 for check in verified)
    providers: dict[str, int] = {}
    flags: dict[str, int] = {}
    for check in verified:
        name = check.provider or "?"
        providers[name] = providers.get(name, 0) + 1
        for flag in check.flags:
            key = flag.split(":", 1)[0].split(" (", 1)[0]
            flags[key] = flags.get(key, 0) + 1
    delta = actual - predicted
    scale = max(abs(predicted), abs(actual), 1e-12)
    return VerifySummary(
        selected=len(checks),
        verified=len(verified),
        missing=sum(1 for check in checks if check.status == "missing"),
        unmatched=sum(1 for check in checks if check.status == "unmatched"),
        errors=sum(1 for check in checks if check.status == "error"),
        mismatches=sum(1 for check in checks if check.status == "mismatch"),
        input=input_tokens,
        output=output_tokens,
        cache_read=cache_read,
        cache_write=cache_write,
        total_input=total_input,
        predicted_cost=predicted,
        actual_cost=actual,
        cache_discount=discount,
        f=(cache_read / total_input) if total_input else 0.0,
        R=(output_tokens / total_input) if total_input else 0.0,
        fw=(cache_write / total_input) if total_input else 0.0,
        predicted_c_eff=(predicted / total_input * 1e6) if total_input else None,
        actual_c_eff=(actual / total_input * 1e6) if total_input else None,
        delta_usd=delta,
        delta_pct=(delta / scale * 100) if (predicted or actual) else None,
        providers=providers,
        flags=flags,
    )


def verify_generations(
    records: Iterable[SessionRecord],
    *,
    client: OpenRouterClient,
    last: int | None = None,
    since: str | None = None,
    until: str | None = None,
    models: Sequence[str] = (),
    routed_models: Sequence[str] = (),
    exclude_models: Sequence[str] = (),
    providers: Sequence[str] = (),
    projects: Sequence[str] = (),
    exclude_projects: Sequence[str] = (),
    tolerance: float = DEFAULT_TOLERANCE,
    progress: Callable[[SessionRecord], None] | None = None,
) -> VerifyResult:
    if tolerance < 0 or tolerance >= 1:
        raise ConfigError("--tolerance must be >= 0 and < 1")
    selected = select_verifiable(
        records,
        last=last,
        since=since,
        until=until,
        models=models,
        routed_models=routed_models,
        exclude_models=exclude_models,
        providers=providers,
        projects=projects,
        exclude_projects=exclude_projects,
    )
    resolver = _EndpointResolver(client)
    checks: list[GenerationCheck] = []
    for record in selected:
        if progress is not None:
            progress(record)
        checks.append(_check(record, client=client, resolver=resolver, tolerance=tolerance))
    return VerifyResult(
        checks=tuple(checks),
        summary=_summary(checks),
        filters={
            "last": last,
            "since": since,
            "until": until,
            "models": list(models),
            "routed_models": list(routed_models),
            "exclude_models": list(exclude_models),
            "providers": list(providers),
            "projects": list(projects),
            "exclude_projects": list(exclude_projects),
            "tolerance": tolerance,
        },
    )
