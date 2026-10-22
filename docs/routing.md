# Routing

`cheapest` ranks the endpoints of one model by the real cost of your token mix
(`C_eff`, see [How it works](how-it-works.md)).

```bash
openrouter-routing cheapest deepseek/deepseek-v4-flash --mix mix.json
openrouter-routing cheapest deepseek/deepseek-v4-flash --mix mix.json --json
openrouter-routing cheapest deepseek/deepseek-v4-flash --mix mix.json --order
openrouter-routing cheapest deepseek/deepseek-v4-flash --mix mix.json --provider-json
```

Without `--mix`, built-in default `f`/`R`/`fw` values are used; pass `--mix` or
`--f`/`--R`/`--fw` to override them. Ranking filters default from `[rank]` and
can be overridden per call. `~author/model-latest` aliases are resolved before
endpoints are fetched.

## Filters

```bash
--healthy-only                 # status == 0 only
--require-cache                # drop endpoints without cache pricing
--quant fp8,fp16               # restrict quantization
--min-tps 40 --tps-pct p90     # throughput floor
--max-ttft 6 --ttft-pct p90    # time-to-first-token floor
--prefer                       # also emit OpenRouter's soft preferred_* cutoffs
--max-price-from N             # size max_price from the Nth ranked endpoint
```

Throughput and latency filters compare p50 or p90 stats. They need an
authenticated call (`OPENROUTER_API_KEY` or `~/.pi/agent/auth.json`); without a
key the stats are null and the filters cannot be applied.

## Provider JSON

`--provider-json` prints a ready routing block:

```json
{
  "provider": {
    "order": ["deepinfra/fp8", "sail-research/fp4", "cloudflare"],
    "allow_fallbacks": true,
    "max_price": { "prompt": 0.101, "completion": 0.15 },
    "preferred_min_throughput": { "p90": 40 },
    "preferred_max_latency": { "p90": 6 }
  }
}
```

## Semantics

- `order` is tried first and in sequence. Keep `allow_fallbacks: true` so an
  outage degrades to the next endpoint instead of failing.
- `max_price` is a drift guardrail, not an optimizer. It compares plain
  prompt/completion prices, so size it from a later endpoint with
  `--max-price-from`.
- `preferred_*` is a soft preference: endpoints that miss it are demoted, not
  dropped. `--min-tps` / `--max-ttft` are hard filters in this tool.
- OpenRouter's provider **sticky routing is disabled when `provider.order` is
  set**. The order still keeps you on the chosen endpoint; the tradeoff is worth
  documenting per preset and may become a selectable routing mode later.

## Storing the result

`provider.order` can be pinned in an OpenRouter preset (see
[Presets](presets.md)) or, in a later phase, in a local pi `models.json`.
