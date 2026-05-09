# The cold-scan time estimator

`absentia est` predicts how long a cold scan will take *before* you
run one. It walks the corpus, applies a calibrated cost model, and
prints a jobs-vs-time table. The model is simple math, not magic
— this doc explains the math, the calibration, and how accurate
you should expect the result to be.

## What the output means

```
absentia est — cold-scan estimate for ~/code/redis

Files               900   (12.2 MB)
By language
               c                750 files (11.9 MB)
               python            43 files (217.4 KB)
               bash              69 files (34.8 KB)
               ruby               9 files (23.0 KB)
               cpp                8 files (13.3 KB)
               lua               20 files (11.6 KB)
               javascript         1 files (953 B)

Total check estimate      ~0.9 s ± 0.0 s   (high confidence)
  components             parse 1.2 s + mine 0.1 s at default jobs (5) · observed
  ground-truth from prior cold scan

Single-process baseline   1.3 s
At default jobs (= 5)       ~1.2 s   (1.07× speedup, 11% efficiency)
Last actual cold scan     0.9 s   (from .absentia/last_run.json — ground truth, jobs=1)
Last cold-scan stage breakdown
               walk          0.0 s   (enumerate files; serial)
               parse         0.8 s   (scales with --jobs)
               store         0.0 s   (sqlite commit; serial)
               mine          0.1 s   (capped at 4 threads)
               finalize      0.0 s   (dedup + commit; serial)

    jobs    parse        +mine(obs)  check     speedup   efficiency
       1       1.3 s   +  0.1 s      1.3 s    1.00×        100%
       2       1.0 s   +  0.1 s      1.0 s    1.29×         65%
       4       0.9 s   +  0.1 s      1.0 s    1.37×         34%
       8       1.1 s   +  0.1 s      1.1 s    1.17×         15%
      10       1.2 s   +  0.1 s      1.2 s    1.07×         11%

Cost model:    p = 0.55, M-series baseline (uncalibrated; expect ±2-4× error).
               Run `absentia est --recalibrate` for machine-specific accuracy.
Methodology:   docs/explanation/estimator.md
```

Four blocks worth understanding:

- **Top:** what's being scanned. File count and bytes per language
  drive the cost model.
- **Headline:** "Total check estimate" is the single number you
  came here for — predicted total `absentia check` time at default
  jobs, with a ± confidence band. The label after the band is one
  of `high` (±5–18%), `medium` (±18–30%), or `low` (>30%). The
  "components" line tells you whether the mining tail came from a
  prior cold scan (`observed`), the calibrated linear model
  (`estimated`), or aggregated runs (`aggregated from N prior
  runs`).
- **Middle:** the per-stage numbers. Single-process baseline is
  the predicted time at `--jobs 1`; "default jobs" is what you
  get if you run plain `absentia check`. The "Last actual cold scan"
  + "Last cold-scan stage breakdown" lines appear when
  `.absentia/last_run.json` exists, exposing where time actually
  went on the prior run.
- **Bottom:** the jobs-vs-time table at powers of two up to your
  CPU's core count. The `+mine(obs)` or `+mine(est)` column shows
  the serial mining tail (mine + finalize, doesn't scale with
  workers); `check` totals it with the per-jobs parse estimate.
  Speedup and efficiency describe the parse stage only — past 2–4
  workers, additional cores buy less (see *Tapering efficiency*
  below).

## The cost model

Three components, each one straightforward:

### 1. Per-language throughput (bytes/sec at `--jobs 1`)

Different languages cost different amounts to parse. C has deeper
ASTs and bigger translation units; Python and Bash are lighter.
The estimator carries a per-language bytes/sec table — calibrated
from real benchmark scans on canonical corpora.

For a multi-language project, the predicted single-process time is
just the sum across languages:

```
serial_time = Σ (language_bytes / language_throughput)
```

### 2. Amdahl's law for parallel speedup

Absentia's pipeline has a parallel part and a serial tail. The
**parallel part** (parse + extract) runs per-file across a worker
pool. The **serial tail** is everything that runs once over the
whole entity collection after extraction:

- **Sibling-test enrichment** (corpus-wide; needs the full entity
  set to answer "does this function have a test?")
- **Frequency mining** (decorator, calls, parent_class,
  sibling_test) over each selector's groups
- **Symmetry-pair detection** — definition-level (class/file
  scope) and call-pair (function scope) — including auto-mining
  pairs from the corpus
- **Cross-strategy gap deduplication** to collapse gaps that
  multiple mining strategies independently flag
- **Storage commit** (SQLite single-writer; can't be parallelized)

None of these scale with parse parallelism. Amdahl's law turns
this split into a speedup curve:

```
speedup(N) = 1 / ((1 − p) + p/N)
```

Where `p` is the parallelizable fraction. The default is `p = 0.55`
(an architectural estimate reflecting that mining + storage +
finalize together dominate the serial tail post-optimization), but
**calibration fits ``p`` from your machine's actual scan times** at
jobs ∈ {1, 2, 4, 8} and stores the fitted value. The output labels
it `(fitted)` when it diverges from the architectural default.

Why fit instead of bake in 0.55? The actual `p` depends on real
factors that vary per machine: I/O subsystem, scheduler, NUMA,
thermal, container limits. Small corpora on fast machines often
show much lower `p` because the serial pipeline tail dominates the
parallel gains. Validated against Dev-Dashboard (week of 2026-05-05,
*before mining-stage parallelism + the 30× symmetry refactor
shipped*): fitted `p = 0.36` (vs. baked 0.55) — accurate because
Dev-Dashboard scans in <1 s and process-spawn overhead eats into
the parallel fraction. The same validation re-run on current
optimized code would show a different `p` (mining is no longer
the dominant serial tail it was); the example is kept for its
illustrative shape, not as a current measurement.

Notice the asymptote: as `N → ∞`, speedup → `1 / (1 − p)`.
**You can never get more than that**, no matter how many cores you
throw at it. The serial tail wins eventually.

| jobs | speedup | parallel efficiency |
|---:|---:|---:|
|  1 | 1.00× | 100% |
|  2 | 1.38× |  69% |
|  4 | 1.70× |  43% |
|  8 | 1.93× |  24% |
| 16 | 2.06× |  13% |
| 32 | 2.14× |   7% |
| ∞  | 2.22× |   0% |

This is the "tapering efficiency" — past 2–4 cores, additional
workers contribute much less. Real absentia defaults to half your
detected cores, which trades efficiency for absolute speedup: on a
10-core machine the default `jobs=5` lands near 1.78× wall-clock
at ~36% efficiency — close to the asymptote without paying for
workers that barely contribute.

### 3. Worker-startup overhead + serial-fallback clamp

Spawning a `multiprocessing` worker takes ~60 ms (process spawn
+ tree-sitter grammar load) on M-series — measured 58-75 ms on a
10-core MacBook 2026-05-07. The estimator adds back
`(jobs − 1) × 0.06 s` to the parallel time, then clamps the result
to be no worse than serial:

```
parallel_time = min(serial_time,
                    serial_time / speedup(N) + (N − 1) × 0.06)
```

The clamp matters because real absentia has a serial-fallback escape
hatch: when a chunk has fewer files than `jobs × 4`, it stays
single-process. So on small corpora, "asking for 8 workers" doesn't
actually spawn them — and the estimator must match that behavior
or it overstates cost.

When every row in the jobs-vs-time table clamps to 1.00× speedup,
the corpus is too small for parallelism to pay off. The output
appends a footer note explaining this so the flat speedup column
isn't read as a bug.

## Calibration

The bytes/sec coefficients in the cost model are M-series MacBook
baselines. Out of the box, they can be off by 2–4× on different
hardware (especially for codebases with lots of small files, where
per-file overhead matters more than per-byte cost).

`absentia est` corrects for this with a one-time calibration on
first run.

### What calibration does

Three measurement passes, all run in a throwaway state dir so your
real `.absentia/` cache isn't polluted (and so every scan is cold):

1. **Validate the corpus.** Walk the chosen path (default: the
   directory you ran `absentia est` in). Refuse if it has fewer than
   30 files or less than 100 KB total — below that, fixed pipeline
   overhead dominates the timing signal and the result would be
   noise. Pass `--use-synthetic` to calibrate against a bundled
   ~180 KB Python corpus instead, useful when your cwd is empty.

2. **Speedup curve at jobs ∈ {1, 2, 4, 8}** (or fewer points on a
   small machine). Use `jobs=1` time + the M-series baseline to
   compute `machine_speed_factor = predicted / actual`. Use the
   speedup curve to fit Amdahl's `p` via grid search (least
   squares over `p ∈ [0.20, 0.99]`).

3. **Per-language BPS for languages with ≥500 KB share.** Each
   eligible language gets its own jobs=1 cold scan with the config
   narrowed to that language. The measured `bytes / elapsed` is its
   calibrated BPS. Below 500 KB the timing signal is noise-
   dominated, so smaller languages fall back to the global
   `machine_speed_factor` × baseline scaling.

The full result is cached at `~/.absentia/calibration.json`:

```json
{
  "calibrated_at": "...",
  "absentia_version": "...",
  "core_count": 10,
  "machine_speed_factor": 0.26,
  "calibration_corpus_path": "...",
  "calibration_files": 395,
  "calibration_bytes": 5_500_000,
  "calibration_duration_s": 0.79,
  "amdahl_p": 0.36,
  "jobs_curve_observed": [[1, 0.79], [2, 0.65], [4, 0.55], [8, 0.55]],
  "per_language_bps": {"python": 6443353, "javascript": 3476173},
  "mining_seconds_per_byte": 1.1e-7,
  "calibration_corpus_languages": ["python", "javascript", "bash"]
}
```

Future estimates use a layered policy: per-language overrides win
where they exist; languages that *appeared* in the calibration
corpus (recorded in `calibration_corpus_languages`) get
global-speed-factor scaling on the M-series baseline; languages
that didn't get the baseline only. The fitted `p` flows into the
Amdahl curve. `mining_seconds_per_byte` lets the estimator predict
the mining tail before the user runs check; once `~/.absentia/runs.jsonl`
has ≥3 fresh runs of compatible cores+version, that aggregated
value supersedes this one.

### When calibration re-prompts

The cache becomes stale (and `absentia est` re-prompts you on next
invocation) when:

- **Absentia's version changed.** Extractors may have shifted; the
  baseline coefficients no longer match. This also catches
  pipeline additions like new mining strategies (symmetry, call
  pairs, sibling-test enrichment) — fresh calibration absorbs the
  new pass costs automatically into the fitted `p` and the
  machine_speed_factor.
- **Core count changed.** Most likely a different machine; the
  cached numbers don't apply.
- **The cache is older than 90 days.** Catches drift from OS
  upgrades, thermal degradation, dying SSDs, and similar slow
  hardware changes that don't trigger the version-or-cores check.
- **You pass `--recalibrate`.** Manual override.

If your `~/.absentia/calibration.json` is missing, `absentia est`
treats it as first-run.

### Skipping calibration

In non-interactive contexts (CI, piped output), `absentia est` skips
prompts entirely. You'll see a one-line note at the top of the
output (`(running uncalibrated — pipe to a terminal or run`
`` `absentia est` `` ` interactively to calibrate)`) and the M-series
baselines are used.

To run uncalibrated even in a TTY, answer `n` to the first-run
prompt; the answer isn't remembered, so you'll be re-prompted next
invocation.

## Accuracy expectations

| State | Expected error |
|---|---:|
| Uncalibrated (first run, M-series defaults) | ±2–4× |
| Calibrated, similar codebase shape | ±10–25% |
| Calibrated, very different codebase shape | ±50% |

Validated post-optimization (2026-05-07, 10-core M-series
MacBook), jobs=1 baseline so the comparison doesn't depend on
parallelism math:

| Scenario | Predicted | Actual | Error |
|---|---:|---:|---:|
| Uncalibrated, redis (~12 MB, 900 C files) | 1.3 s | 0.9 s | ~1.4× |
| Uncalibrated, Linux kernel (~1.3 GB, 65 k C files) | 2 m 20 s | 1 m 34 s | ~1.5× |

Both fall well within the ±2–4× uncalibrated expectation —
M-series defaults are conservative for a reason (different
machines and language mixes can hit the wider end of the band).
Calibration tightens this to ±10–25% by fitting `M_SERIES_BPS`
coefficients to your hardware and `p` to your workload's serial
tail.

The estimator never claims to be a benchmark. It's a "should I go
grab coffee?" predictor. If you need exact wall-clock numbers, run
`absentia check --jobs 1` and time it.

## How to recalibrate

```bash
absentia est --recalibrate              # re-prompt and re-measure
absentia est --recalibrate --use-synthetic   # calibrate against bundled corpus
```

Or delete the cache file and run `absentia est` again:

```bash
rm ~/.absentia/calibration.json
absentia est
```

If you change machines, upgrade absentia, or 90 days pass, the cache
invalidates itself — you'll be re-prompted automatically.

## Where this surfaces in normal flows

Once calibration runs, the cost model is in service across absentia:

- **`absentia est`** — the dedicated UI, prints the full ASCII
  jobs-vs-time table.
- **`absentia check`** — one-line preamble before scanning
  (`Scanning N files (M MB) — est. ~Xs at jobs=Y`). Skipped when
  output is JSON, when `--quiet` is passed, or when stderr isn't a
  terminal (CI logs stay clean).
- **`absentia init`** — first-scan estimate as a footer between the
  init confirmation and the "Run absentia check" line.
- **TUI** — transient `estimating ~Xs · scanning…` subtitle on
  cold scans, replaced by the post-scan stats once the scan
  completes.

All four paths share the same `quick_estimate_line()` helper and
read the same `~/.absentia/calibration.json` cache.

## Status and future work

Shipped:

- ✅ Per-language calibration (≥ 500 KB threshold for clean signal)
- ✅ Amdahl's `p` fitted from observed multi-jobs curve
- ✅ Bundled synthetic corpus + `--use-synthetic` flag
- ✅ Age-based re-prompt (90 days)
- ✅ Stale-detection on version + core-count change
- ✅ Reality-check ground truth from prior `last_run.json`
- ✅ Calibrated mining-tail prediction
  (`mining_seconds_per_byte`) so the predictor can estimate full
  check time before the user runs check
- ✅ Headline total + confidence band (`high`/`medium`/`low ± rel_err`)
- ✅ Per-stage breakdown when prior cold-scan timings exist
- ✅ Pipeline-overhead subtraction in calibration so synthetic and
  small-corpus calibrations don't fall victim to fixed-overhead
  noise
- ✅ Runs-log aggregation (`~/.absentia/runs.jsonl`) — every
  `absentia check` automatically refines `mining_seconds_per_byte`,
  no explicit recalibration needed; confidence band tightens with
  sample count

What's still rough:

- The 500 KB minimum-bytes-per-language threshold for
  `per_language_bps` is conservative. The runs-log aggregation
  doesn't yet include per-language bps because attributing parse
  wall-time to a single language is hard in a multi-language run.
  A future pass could mine single-language-dominant runs as
  per-language samples.
- `p` is fit on a single corpus per calibration. The runs log
  could feed a multi-corpus bootstrap fit that produces a
  confidence interval rather than a point estimate.
- The model is linear in bytes for both parse and mining. Mining
  has known superlinear behavior on huge corpora (pair counts
  scale with name diversity), which the linear model under-
  predicts above ~50 MB per language. A piecewise-linear or
  log-linear fit would track that better.
- No telemetry, by design. The runs log is machine-local and
  never leaves the machine. Aggregating across users (privacy-
  respecting opt-in) would let us refine the M-series baseline
  seed values over time, but that's a separate commitment.
