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

Cost model:    p = 0.80, calibrated on this machine (2026-05-05).
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

Lacuna's pipeline is roughly 80% parallelizable (parse + extract,
which is per-file independent and runs across a worker pool) and
20% serial (group + mine + storage write, which can't be sped up
by adding cores). Amdahl's law turns that into a speedup curve:

```
speedup(N) = 1 / ((1 − p) + p/N)        where p = 0.80
```

This is what your speedup column shows. Notice the asymptote: as
`N → ∞`, speedup → `1 / (1 − p) = 5×`. **You can never get more
than 5× speedup**, no matter how many cores you throw at it. The
serial tail wins eventually.

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

1. Walk a corpus you choose (default: the directory you ran
   `lacuna est` in). Validate it has at least 30 files and 100 KB.
2. Predict its serial time using the M-series baseline coefficients.
3. Run an actual single-process scan in a temporary state dir
   (so your real `.lacuna/` cache isn't polluted, and the scan is
   guaranteed cold).
4. Compute `machine_speed_factor = predicted_time / actual_time`.
   - Factor < 1 means your machine is slower than the baseline.
   - Factor > 1 means faster.
5. Cache the result at `~/.lacuna/calibration.json`.

Future estimates multiply all baseline bytes/sec values by this
factor before computing — a single-knob correction that captures
the dominant source of error.

### When calibration re-prompts

The cache becomes stale (and `lacuna est` re-prompts you on next
invocation) when:

- **Lacuna's version changed.** Extractors may have shifted; the
  baseline coefficients no longer match.
- **Core count changed.** Most likely a different machine; the
  cached numbers don't apply.
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
```

Or delete the cache file and run `lacuna est` again:

```bash
rm ~/.lacuna/calibration.json
lacuna est
```

If you change machines or upgrade lacuna, the cache invalidates
itself — you'll be re-prompted automatically.

## Future work

The current model has known limits, addressed in roughly this order:

- **Per-language calibration.** A single `machine_speed_factor`
  applies uniformly to every language. A real model would measure
  per-language throughput (different machines accelerate different
  parsers differently).
- **Amdahl's `p` from observation.** Today `p = 0.80` is baked in.
  Multi-jobs calibration runs (jobs ∈ {1, 2, 4, 8}) would let us fit
  `p` for each user's specific hardware + I/O subsystem.
- **Bundled synthetic corpus** + `--use-synthetic` flag. Lets you
  calibrate from an empty directory.
- **Age-based re-prompt.** Re-calibrate after 90 days to catch drift
  from OS updates, thermal degradation, dying SSD, etc.

These improvements refine the model without changing the structure;
the math above stays the same.
