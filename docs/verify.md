# Verify

`verify` closes the loop between the cost model and what OpenRouter actually
billed. It reads recent pi messages that carry a `responseId`, fetches
`GET /api/v1/generation?id=<responseId>`, and compares the predicted cost with
the generation's `total_cost`.

```bash
openrouter-routing verify --last 20
openrouter-routing verify --last 20 --json
openrouter-routing verify --since 2026-10-01T00:00:00Z --model '@preset/ds4-flash'
openrouter-routing verify --project ~/code --provider openrouter
```

`verify` needs an authenticated OpenRouter key (`OPENROUTER_API_KEY` or the
`openrouter` entry in `~/.pi/agent/auth.json`). Unlike `measure`, it always
touches the network.

## What is compared

For each generation:

1. The session record supplies the four token counters (`input`, `cacheRead`,
   `cacheWrite`, `output`).
2. The generation record supplies the provider and upstream model. `verify`
   finds that provider in the model's endpoint list and uses the endpoint's
   prompt/completion/cache-read/cache-write prices.
3. `predicted` is the same `C_eff` billing formula applied to those tokens and
   prices. `actual` is `total_cost` from the generation record.
4. `C_eff` is normalized per 1M input tokens:
   `cost / (input + cacheRead) * 1e6`.

This catches the assumptions the routing plan depends on: endpoint price
selection, cache-read discounts, cache-write pricing, and the token counters pi
recorded.

## Selection

| Flag | Meaning |
|---|---|
| `--last N` | the N most recent generations with a `responseId` |
| `--since` / `--until` | inclusive ISO bounds |
| `--model` | requested model (substring/glob, repeatable) |
| `--routed-model` | upstream `responseModel` (repeatable) |
| `--exclude-model` | exclude requested models |
| `--provider` | pi provider |
| `--project` | recursive session cwd root |
| `--exclude-project` | recursive cwd exclusion |
| `--tolerance` | relative cost mismatch threshold (default `0.05`) |

Without `--since`/`--until`, `--last` defaults to 20 so a casual run does not
fetch thousands of generation records. With an explicit time window, all
matching generations are verified unless `--last` is also passed. Filters use
the same matching rules as [`measure`](measure.md); project roots are recursive
and `/` is rejected.

## Output

The table shows the generation time, provider, upstream model, token counters,
predicted and actual cost, and status. Flags below a row explain likely causes
of a difference:

- generation cached fewer tokens than pi: cache TTL, minimum cacheable prompt
  size, or provider fallback;
- cache reads or writes with no matching endpoint price;
- cache-write price above the input price;
- no cache reads on a small prompt, or no cache reads at all;
- cache reads with no reported cache discount;
- BYOK/upstream cost differing from `total_cost`;
- prompt/completion token counts differing (truncation or tokenizer
  normalization).

The summary aggregates the verified rows:

```text
generations: 12 verified, 1 missing/expired, 0 unmatched, 0 error(s), 0 mismatch(es)
tokens     : input 81,420  cacheRead 912,300  cacheWrite 0  output 6,100
mix        : f=0.9180  R=0.00614  fw=0.00000
cost       : predicted $0.412300  actual $0.412300  delta $+0.000000
C_eff      : predicted $0.414963  actual $0.414963 per 1M input tokens
cache      : discount $2.310400
providers  : Cloudflare=7, DeepInfra=4, Decart=1
```

That summary is also the weekly cost check: run `verify --since` with a
seven-day bound to get the realized cache-hit share (`f`), `R`, `fw`, actual
`C_eff`, cache discount, and provider distribution for the week.

Use `--json` for a stable machine-readable report with schema
`openrouter-routing/verify/v1`, the resolved filters, the summary, and one entry
per generation.

## Missing records and exit status

Generation records expire, and a missing record is normal for older sessions. A
`404` is reported as `missing` instead of aborting the run. If some rows verify,
the command exits 0 and warns about missing/expired records. It exits 1 when no
generation could be verified, or when a lookup fails for a reason other than
404. Cost mismatches are findings, not command failures, and also produce a
warning.

## Checking order versus preferred routing

`order` pins the provider list and disables OpenRouter's sticky routing;
`preferred` keeps sticky routing and uses `sort: price` plus soft performance
floors. To validate either assumption with real data:

1. Run `verify` over the last week (or since the last routing change).
2. Compare the provider distribution and actual `C_eff` with the endpoint that
   `refresh --compare` predicted.
3. For `order`, the expected provider is the first still-available endpoint in
   the current order. For `preferred`, provider stickiness is expected to keep
   more requests on one provider; use the distribution rather than a single
   predicted endpoint.

A mismatch between the predicted and actual cost is usually a cache assumption,
not a routing bug; the flags call out the likely cause.
