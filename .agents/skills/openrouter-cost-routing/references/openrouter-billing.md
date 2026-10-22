# OpenRouter billing and routing references

Last checked against the public API/docs in October 2026. Re-check before
relying on a field; OpenRouter updates the docs and OpenAPI schema.

## Token classes and endpoint pricing

The endpoint list is available without a key:

```text
GET https://openrouter.ai/api/v1/models/<author>/<slug>/endpoints
```

Relevant per-endpoint fields:

| field | meaning |
|---|---|
| `pricing.prompt` | fresh input, USD per token |
| `pricing.completion` | output, USD per token |
| `pricing.input_cache_read` | cache-hit input, USD per token |
| `pricing.input_cache_write` | cache-write input, USD per token |
| `quantization` | e.g. `fp8`, `fp4`, `unknown` |
| `supports_implicit_caching` | provider caches without explicit breakpoints |
| `status` | `0` = ok; negative = degraded/deprecated |
| `uptime_last_1d` | availability percentage |
| `throughput_last_30m` | `{p50,p75,p90,p99}` output tokens/sec |
| `latency_last_30m` | `{p50,p75,p90,p99}` time to first token in **ms** |

`latency_last_30m` and `throughput_last_30m` are null unless the call is
authenticated.

The API returns prices per token; this project multiplies by `1e6` to work in
USD per 1M tokens.

## Usage accounting

OpenRouter always returns usage details. The relevant response shape is:

```json
{
  "usage": {
    "prompt_tokens": 10339,
    "completion_tokens": 60,
    "prompt_tokens_details": {
      "cached_tokens": 10318,
      "cache_write_tokens": 0
    },
    "cost": 0.95,
    "cost_details": {
      "upstream_inference_cost": 19
    }
  }
}
```

- `cached_tokens` = cache reads.
- `cache_write_tokens` = cache writes.
- `cost` = amount charged to the account.
- `cost_details.upstream_inference_cost` = upstream cost; for the generation
  endpoint it is only populated for BYOK requests.

Sources:

- https://openrouter.ai/docs/use-cases/usage-accounting
- https://openrouter.ai/docs/features/prompt-caching

## Generation endpoint

```text
GET https://openrouter.ai/api/v1/generation?id=<generation_id>
```

Fields used for verification: `provider_name`, `model`, `tokens_prompt`,
`tokens_completion`, `native_tokens_prompt`, `native_tokens_completion`,
`native_tokens_cached`, `cache_discount`, `total_cost`,
`upstream_inference_cost`, `is_byok`. The generation endpoint does not expose a
cache-write token field; `verify` reports cache writes from the pi session and
flags an endpoint without a cache-write price. A `404` means the record is
missing or expired and must not abort a whole verification run.

Source: https://openrouter.ai/docs/api-reference/get-a-generation

## Provider routing

Request body field: `provider`.

Supported fields include:

| field | behavior |
|---|---|
| `order` | provider slugs tried in order; outranks normal load balancing |
| `allow_fallbacks` | fall back to other providers when the ordered ones fail |
| `sort` | `price`, `throughput`, `latency`, or `exacto`; disables load balancing |
| `ignore` | hard provider blacklist |
| `only` | hard provider allowlist |
| `quantizations` | restrict precision |
| `max_price` | `prompt`, `completion`, `request`, `image`, `audio`; no cache field |
| `preferred_min_throughput` | soft throughput preference, number or `{p50,p75,p90,p99}` |
| `preferred_max_latency` | soft TTFT preference in seconds, number or percentiles |

There is no cache-aware `sort` and no cache field in `max_price`. That is why
the optimal cache-aware routing is computed offline and expressed as `order`.

Important: OpenRouter's provider **sticky routing** keeps subsequent requests on
the same provider to preserve the prompt cache, but it is not used when a manual
`provider.order` is specified. Sticky sessions expire after 10 minutes of
inactivity. Document this tradeoff when choosing `order` vs `sort`.

Source: https://openrouter.ai/docs/features/provider-routing

## Prompt caching and cache writes

- Caching can be implicit (OpenAI/DeepSeek/Gemini-style) or explicit
  (`cache_control` breakpoints).
- Cache reads are usually discounted; cache writes can cost more than normal
  input (e.g. 1.25× or 2.5× for some Anthropic/OpenAI configurations).
- Minimum cacheable prompt sizes and TTLs vary by provider. Assume `f` from an
  old session may not hold after long gaps or short prompts.
- Sticky routing is intended to maximize cache hits, subject to the `order`
  caveat above.

Source: https://openrouter.ai/docs/features/prompt-caching

## Presets

Public API surface:

```text
GET  /api/v1/presets
GET  /api/v1/presets/{slug}
GET  /api/v1/presets/{slug}/versions
GET  /api/v1/presets/{slug}/versions/{version}
POST /api/v1/presets/{slug}/chat/completions
POST /api/v1/presets/{slug}/messages
POST /api/v1/presets/{slug}/responses
```

There is no `PUT`/`PATCH`/`DELETE` for presets. POSTing a chat-completions body
creates a new designated version from the overlapping config fields; `messages`
and other non-config fields are ignored. Always send the complete config, not
just `provider.order`.

Source: https://openrouter.ai/docs/features/presets

## OpenAPI

`https://openrouter.ai/openapi.json` is the authoritative machine-readable
schema for endpoints, request fields, and response shapes. Check it when adding
a new API call.
