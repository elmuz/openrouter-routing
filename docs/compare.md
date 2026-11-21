# Compare models

`compare` answers one question: **given the token mix I actually generate, which
of two models is cheaper tomorrow?** It ranks the endpoints of both models with
the same mix and the same filters, then reports the cheapest endpoint of each
and the cost difference.

```bash
openrouter-routing compare deepseek/deepseek-v4-flash qwen/qwen3-235b-a22b --mix mix.json
openrouter-routing compare deepseek/deepseek-v4-flash qwen/qwen3-235b-a22b --mix mix.json --json
```

The mix is a **workload fingerprint**, not a model fingerprint. Measure it once
without a model filter (or with a project filter) and reuse it for any model:

```bash
openrouter-routing measure --days 7 --json > mix.json
openrouter-routing compare MODEL_A MODEL_B --mix mix.json
```

This is what makes the command useful for a model that has no history yet: a
new model only needs an endpoint list and prices, not sessions. The comparison
is "same work, different model", not "my history with A versus my history with
B".

## Output

```text
mix: f=0.9206 R=0.00554 fw=0.00000 (8,249 requests)   snapshot 2026-02-14T10:00:00+00:00

model                        best endpoint        C_eff   mix cost  f_used     ctx  vs best
deepseek/deepseek-v4-flash   deepinfra/fp8       0.0412   0.123400   0.921  128000        —
qwen/qwen3-235b-a22b         novita/fp8          0.0578   0.173200   0.921  256000   +40.4%

deepseek/deepseek-v4-flash is 28.7% cheaper than qwen/qwen3-235b-a22b at this mix.

Note: Rough estimate, not a prediction: ...
```

- `C_eff` is the cost of 1M input tokens at the mix (see
  [How it works](how-it-works.md)).
- `mix cost` is the cost of the token counters in the mix file. When the mix
  has no counters, one million input tokens are synthesized from `f`/`R`/`fw`.
- `f_used` is the cache-hit share actually applied to that endpoint. A
  no-cache endpoint is scored at `f=0`, so a large `mix cost` can mean "this
  endpoint cannot cache", not "this model is expensive".
- `vs best` is the cost relative to the cheaper of the two.
- `delta_pct` is `(expensive - cheap) / expensive`, so "A is 40% cheaper than
  B" means A costs 60% of B.

## Filters

`compare` accepts the same ranking filters as `cheapest`:

```bash
--healthy-only                 # status == 0 only
--require-cache                # drop endpoints without cache pricing
--quant fp8,fp16               # restrict quantization
--min-tps 40 --tps-pct p90     # throughput floor
--max-ttft 6 --ttft-pct p90    # time-to-first-token floor
```

The filters are applied to both models, so the comparison stays like-for-like.
Throughput and latency filters need an authenticated call
(`OPENROUTER_API_KEY` or `~/.pi/agent/auth.json`).

## JSON

```json
{
  "mix": {"f": 0.9206, "R": 0.00554, "fw": 0.0, "requests": 8249, "...": "..."},
  "snapshot": "2026-02-14T10:00:00+00:00",
  "a": {
    "model": "deepseek/deepseek-v4-flash",
    "endpoint": "deepinfra/fp8",
    "c_eff": 0.0412,
    "estimated_cost_usd": 0.1234,
    "f_used": 0.9206,
    "cached": true,
    "ctx": 128000,
    "quant": "fp8",
    "status": 0,
    "uptime": 99.9
  },
  "b": {"...": "..."},
  "cheaper": "a",
  "cheaper_model": "deepseek/deepseek-v4-flash",
  "delta_usd": 0.0498,
  "delta_pct": 28.75,
  "ratio": 1.40,
  "caveat": "Rough estimate, not a prediction: ..."
}
```

`cheaper` is `"a"`, `"b"`, or `"tie"`. `delta_usd` and `delta_pct` are always
non-negative; `cheaper` says which side wins. `ratio` is
`expensive / cheap`.

## Caveats

The command always prints this warning, and the JSON carries it in `caveat`:

> Rough estimate, not a prediction: both models are scored with the same token
> mix (f/R/fw) and the same token counts. It assumes their tokenizers produce
> the same number of tokens for your text, that prompt caching behaves the same
> way (TTL, minimum prompt size), and that output length is unchanged. It does
> not price quality, retries, or context-window limits, and prices are a
> snapshot that can change.

In practice:

- **Tokenizers differ.** The mix is in tokens, not text. A model with a more
  efficient tokenizer uses fewer tokens for the same prompt, so its real cost
  is lower than the comparison says (and vice versa).
- **Caching differs.** `f` was measured on a model that caches. If the new
  model cannot cache, it is scored at `f=0` and looks much more expensive. If
  it caches with a different TTL or minimum prompt size, the realized `f` may
  be lower than measured.
- **Output length is held constant.** A chattier model emits more output tokens
  for the same task. `R` is not adjusted per model.
- **Quality is not priced.** A cheaper model can be worse. The command says
  "cheaper at this mix", never "better".
- **Context limits are not enforced.** Check `ctx` in the output; a model whose
  context is smaller than your workload is not a real option.
- **Prices are a snapshot.** Endpoint sets and prices change; the `snapshot`
  field records when the comparison was made.

Use [`verify`](verify.md) on real generation records to check whether the
predicted cost matches what OpenRouter actually billed.
