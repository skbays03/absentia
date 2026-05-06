# The cold-scan time estimator

`lacuna est` predicts how long a cold scan will take *before* you
run one. It walks the corpus, applies a calibrated cost model, and
prints a jobs-vs-time table. The model is simple math, not magic
— this doc explains the math, the calibration, and how accurate
you should expect the result to be.

## What the output means

```
lacuna est — cold-scan estimate for /Users/shawn/myrepo

Files               395   (5.5 MB)
By language
               python           316 files (4.5 MB)
               javascript        40 files (953.5 KB)
               bash              27 files (138.1 KB)

Single-process baseline   0.8 s
At default jobs (= 5)       ~0.8 s   (1.00× speedup, 10% efficiency)
Last actual cold scan     0.9 s   (from .lacuna/last_run.json — ground truth)

    jobs    est. time   speedup   efficiency
       1        0.8 s    1.00×        100%
       2        0.6 s    1.28×         64%
       4        0.8 s    1.05×         26%
       8        0.8 s    1.00×         12%
      10        0.8 s    1.00×         10%

Cost model:    p = 0.36 (fitted), calibrated on this machine (2026-05-05).
Methodology:   docs/explanation/estimator.md
```

Three blocks worth understanding:

- **Top:** what's being scanned. File count and bytes per language
  drive the cost model.
- **Middle:** the headline numbers. Single-process baseline is the
  predicted time at `--jobs 1`; "default jobs" is what you get if
  you run plain `lacuna check`. The "Last actual cold scan" line
  appears when `.lacuna/last_run.json` exists and lets you sanity-
  check the prediction against reality.
- **Bottom:** the jobs-vs-time table at powers of two up to your
  CPU's core count. Speedup and efficiency reveal whether more cores
  buy you anything (often they don't past 2–4 — see *Tapering
  efficiency* below).

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

Lacuna's pipeline has a parallel part and a serial tail. The
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

Where `p` is the parallelizable fraction. The default is `p = 0.80`
(an architectural estimate), but **calibration fits ``p`` from your
machine's actual scan times** at jobs ∈ {1, 2, 4, 8} and stores the
fitted value. The output labels it `(fitted)` when it diverges from
the architectural default.

Why fit instead of bake in 0.80? The actual `p` depends on real
factors that vary per machine: I/O subsystem, scheduler, NUMA,
thermal, container limits. Small corpora on fast machines often
show much lower `p` because the serial pipeline tail dominates the
parallel gains. Validated against Dev-Dashboard: fitted `p = 0.36`
(vs. baked 0.80) — accurate because Dev-Dashboard scans in <1 s
and process-spawn overhead eats into the parallel fraction.

Notice the asymptote: as `N → ∞`, speedup → `1 / (1 − p)`.
**You can never get more than that**, no matter how many cores you
throw at it. The serial tail wins eventually.

| jobs | speedup | parallel efficiency |
|---:|---:|---:|
|  1 | 1.00× | 100% |
|  2 | 1.67× |  83% |
|  4 | 2.86× |  72% |
|  8 | 4.00× |  50% |
| 16 | 4.44× |  28% |
| 32 | 4.71× |  15% |
| ∞  | 5.00× |   0% |

This is the "tapering efficiency" — past 4–8 cores, additional
workers contribute less and less. Real lacuna defaults to half
your detected cores, which sits in the sweet spot.

### 3. Worker-startup overhead + serial-fallback clamp

Spawning a `multiprocessing` worker takes ~150 ms (process spawn
+ tree-sitter grammar load). The estimator adds back
`(jobs − 1) × 0.15 s` to the parallel time, then clamps the result
to be no worse than serial:

```
parallel_time = min(serial_time,
                    serial_time / speedup(N) + (N − 1) × 0.15)
```

The clamp matters because real lacuna has a serial-fallback escape
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

`lacuna est` corrects for this with a one-time calibration on
first run.

### What calibration does

Three measurement passes, all run in a throwaway state dir so your
real `.lacuna/` cache isn't polluted (and so every scan is cold):

1. **Validate the corpus.** Walk the chosen path (default: the
   directory you ran `lacuna est` in). Refuse if it has fewer than
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

The full result is cached at `~/.lacuna/calibration.json`:

```json
{
  "calibrated_at": "...",
  "lacuna_version": "...",
  "core_count": 10,
  "machine_speed_factor": 0.26,
  "amdahl_p": 0.36,
  "jobs_curve_observed": [[1, 0.79], [2, 0.65], [4, 0.55], [8, 0.55]],
  "per_language_bps": {"python": 6443353, "javascript": 3476173},
  "...": "..."
}
```

Future estimates use a two-layer policy: per-language overrides win
where they exist; everything else gets `M_SERIES_BPS[lang]` scaled
by `machine_speed_factor`. The fitted `p` flows into the Amdahl
curve.

### When calibration re-prompts

The cache becomes stale (and `lacuna est` re-prompts you on next
invocation) when:

- **Lacuna's version changed.** Extractors may have shifted; the
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

If your `~/.lacuna/calibration.json` is missing, `lacuna est`
treats it as first-run.

### Skipping calibration

In non-interactive contexts (CI, piped output), `lacuna est` skips
prompts entirely. You'll see a one-line note at the top of the
output (`(running uncalibrated — pipe to a terminal or run`
`` `lacuna est` `` ` interactively to calibrate)`) and the M-series
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

Validated against `Dev-Dashboard` (a real Python-heavy project
with 395 files, mostly small):

| Scenario | Predicted | Actual | Error |
|---|---:|---:|---:|
| Uncalibrated | 0.2 s | 0.9 s | ~4× |
| After calibration on Dev-Dashboard itself | 0.8 s | 0.9 s | ~12% |

The estimator never claims to be a benchmark. It's a "should I go
grab coffee?" predictor. If you need exact wall-clock numbers, run
`lacuna check --jobs 1` and time it.

## How to recalibrate

```bash
lacuna est --recalibrate              # re-prompt and re-measure
lacuna est --recalibrate --use-synthetic   # calibrate against bundled corpus
```

Or delete the cache file and run `lacuna est` again:

```bash
rm ~/.lacuna/calibration.json
lacuna est
```

If you change machines, upgrade lacuna, or 90 days pass, the cache
invalidates itself — you'll be re-prompted automatically.

## Where this surfaces in normal flows

Once calibration runs, the cost model is in service across lacuna:

- **`lacuna est`** — the dedicated UI, prints the full ASCII
  jobs-vs-time table.
- **`lacuna check`** — one-line preamble before scanning
  (`Scanning N files (M MB) — est. ~Xs at jobs=Y`). Skipped when
  output is JSON, when `--quiet` is passed, or when stderr isn't a
  terminal (CI logs stay clean).
- **`lacuna init`** — first-scan estimate as a footer between the
  init confirmation and the "Run lacuna check" line.
- **TUI** — transient `estimating ~Xs · scanning…` subtitle on
  cold scans, replaced by the post-scan stats once the scan
  completes.

All four paths share the same `quick_estimate_line()` helper and
read the same `~/.lacuna/calibration.json` cache.

## Future work

The current implementation covers the headline features in the
original design:

- ✅ Per-language calibration (≥ 500 KB threshold for clean signal)
- ✅ Amdahl's `p` fitted from observed multi-jobs curve
- ✅ Bundled synthetic corpus + `--use-synthetic` flag
- ✅ Age-based re-prompt (90 days)
- ✅ Stale-detection on version + core-count change
- ✅ Reality-check ground truth from prior `last_run.json`

What's still rough:

- The 500 KB minimum-bytes-per-language threshold is conservative.
  A more sophisticated model would subtract estimated pipeline
  overhead from the per-language elapsed time, recovering signal
  on smaller language shares.
- `p` is fit on a single corpus. A more rigorous fit would combine
  multiple corpora + bootstrap to give a confidence interval, not
  a point estimate.
- No telemetry / no aggregation. Each user calibrates locally, and
  we don't know if our defaults are good for the population. A
  privacy-respecting opt-in submission would let us refine the
  M-series baselines over time.
