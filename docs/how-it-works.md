# How it works

OpenRouter bills four token classes: prompt, completion, cache read, and cache
write. For agentic work most input tokens are cache hits, so the cache-read
price is the price. OpenRouter's `sort: price` only looks at prompt+completion
and therefore picks the wrong endpoint.

This project measures *your* token mix from pi's session logs, scores every
endpoint with that mix, and emits the `provider.order` that minimizes the real
cost.

## The billing model

OpenRouter bills per request as:

```text
cost = (n_in - n_cr)·p_in + n_cr·p_cr + n_out·p_out + n_cw·p_cw
```

Aggregate over a session (sum tokens, never average per-request ratios) and
normalize on total input tokens:

```text
f  = Σn_cr / Σ(n_in + n_cr)        cache-hit share of input tokens
R  = Σn_out / Σ(n_in + n_cr)       output tokens per input token
fw = Σn_cw / Σ(n_in + n_cr)        cache-write tokens per input token

C_eff = (1-f)·p_in + f·p_cr + R·p_out + fw·p_cw
```

Providers that cannot cache are scored with `f = fw = 0` and `p_cr = p_in`:
for them every input token is a miss.

## Measured mix

Measured on the author's machine (8,249 recorded requests):

```text
fresh input   86,971,668      f  = 0.9206
cache read  1,007,824,815      R  = 0.00554
output         6,067,372      fw = 0
```

The weight breakdown per 1M input tokens is therefore roughly **8% p_in + 92%
p_cr + 0.55% p_out**. Cache-read price dominates. A no-cache endpoint needs about
`1/(1-f)` ≈ 12.6× cheaper input to compete at the global mix, and more in warm
sessions.

`R` matters more for chat-style workloads (short prompts, few cache hits); keep
one mix per workload class. That is why `measure` supports model, project, and
time filters — see [Measure](measure.md).

## Notes and limits

- pi records the requested model, not the upstream provider. It does record
  `responseModel` and `responseId`.
- Cached-token counts depend on pi/provider reporting. Cache TTL, minimum
  cacheable prompt size, truncation, and cache-write pricing can make the
  realized cost differ from the model; validate against
  `GET /api/v1/generation?id=<responseId>` when in doubt. The
  [`verify`](verify.md) command automates that comparison and reports the likely
  cause of a divergence.
- `max_price` has no cache field in OpenRouter's API; that is why the optimal
  cache-aware routing has to be computed offline and expressed as `order`.
- The project targets OpenRouter. The session loader is isolated so another
  harness can be added later without changing the pricing logic.
