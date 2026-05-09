# Why no LLM

Absentia is deliberately not an AI tool. The engine is classical:
tree-sitter parses, frequent-itemset mining counts, plain statistics
rank. There's no language model, no embeddings, no fine-tuning, no
cloud API.

This is a choice, and a counter-cultural one in 2026. This doc
explains why determinism beats inference for absentia's specific job,
and what the tradeoffs are.

## What an AI tool would do here

It's worth being charitable about why someone would expect an LLM in
this product. "Find patterns in code and surface anomalies" sounds
like a paragraph from a foundation-model README. Most modern code
tools — Copilot, Cursor's chat, Sourcegraph Cody, every
"AI code reviewer" product launched in the last three years — would
take this brief and do roughly the same thing:

1. Embed every file's code into a vector
2. Cluster or compare embeddings to find "similar" code
3. Use a language model to *describe* what's "off" about a piece
4. Generate a natural-language explanation per finding

That works, sort of. It's also four reasons absentia doesn't do it.

## What you get from determinism

### Run twice, same answer

Absentia's output is a function of your code. No randomness, no
temperature, no model version. Run absentia at 9 AM and again at
9:01 AM and you get *byte-identical* results unless the code
changed. This matters because:

- CI turns absentia into a real check, not advice that drifts
- Suppression IDs stay stable across runs, so silenced gaps stay silenced
- A regression test for "absentia found this gap last week" is meaningful

Try writing a regression test against an LLM's output. The test passes
or fails depending on the model's mood that minute.

### Free explanations as a byproduct of mining

Every gap absentia shows you traces back to a rule. Every rule traces
back to the members of your codebase that exhibit it. Both traces are
*deterministic facts*, not narrative reconstructions:

```
class delete_user is missing @audit
because 9 of 10 functions in src/api/ have @audit
specifically: create_user, update_user, list_users, get_user,
              deactivate, reactivate, ban, unban, promote
```

You can read that, point at any name in the list, and verify it.
The explanation is *the math*. There's nothing to second-guess.

Compare the LLM equivalent: *"This function appears to be missing an
audit decorator that's commonly used in similar functions for tracking
sensitive operations."* Plausible-sounding, often correct, sometimes
hallucinated. There's no list of names because the model doesn't
*have* a list — it's making the recommendation from a vector cluster.

When the mechanism produces the explanation as a byproduct, the
explanation is exactly as trustworthy as the mechanism. When the
explanation is generated separately, you have two things to trust.

### Instant interactions

Absentia's TUI scans medium codebases in seconds and the Linux
kernel in tens of seconds (~48 s cold, ~24 s warm at default
jobs on a 10-core M-series MacBook). Once a scan finishes, every
keystroke in the UI — filtering, navigating, suppressing,
switching views — runs against an in-memory dataset using set
lookups and dict reads. No model inference, no API call, no
network round-trip, no waiting.

LLM-augmented tools can't do this. Even a fast local model is tens of
milliseconds per token, and any cloud-hosted one is hundreds. That's
fine for "explain this function" but it's not fine for "navigate
through 800 gaps."

### Zero ongoing cost

Absentia runs on your laptop. It doesn't phone home. It doesn't bill
you. There's no rate limit, no token budget, no "you've used 80% of
your monthly allowance" email. Run it on every commit, in every CI
job, on every developer's machine, on every repo you own — the cost
is the disk space for `.absentia/`. State scales roughly linearly
with entity count: single-digit megabytes for medium repos
(1-50 MB on disk), low-hundreds-of-megabytes for kernel-scale
(~700 k entities ≈ 370 MB).

This is harder than it looks for the AI alternative. Even
self-hosted local models impose a real fixed cost in compute and
infrastructure. Cloud-hosted means proprietary code goes to a third
party every time you press save.

### Code never leaves the machine

For a tool whose job is *reading every file in your codebase*, the
where-does-the-data-go question matters. Absentia's answer: nowhere.
Tree-sitter parses locally. SQLite stores locally. Mining computes
locally. Suppressions live locally. There's no telemetry phoning
home, no inference endpoint, no "we use your code to improve our
service" clause.

For proprietary, regulated, or security-sensitive codebases, this
isn't a nice-to-have — it's a requirement. Absentia meets it
trivially because it never had a network code path to begin with.

## The trust argument

The four things above (determinism, free explanations, speed, cost
+ privacy) are individually real but together they add up to one
thing: **a tool you can trust without trust.**

That's the actual pitch. Absentia doesn't ask you to trust it. It asks
you to verify it — and the mechanism is *built so verification is
trivial*. Click on any gap, read the rule it came from, look at the
members of the group, confirm the count. The math is right there.

Every "AI code review" tool sells the opposite: an oracle whose
trustworthiness you take on faith. Sometimes it's right, sometimes
it confidently makes things up, and you can't tell which without
domain knowledge that often exceeds what the tool is supposedly
giving you.

We pitch absentia at the audience that wants the engine where they can
see it. That audience exists, and they're tired of dashboards that
hide the math behind a chat box.

## Where ML *might* still belong

This isn't a holy war. There are places where ML is genuinely the
right tool, and absentia's architecture has clean seams for them:

### Cross-document semantic matching (embeddings, not LLMs)

The personal-knowledge variant of negative-space search — "find
topics I keep reading about but never write notes on" — needs
cross-document similarity. Sentence embeddings handle this well; a
small local embedding model (~100MB) is enough. **This is not the
same as putting an LLM in the engine.** Embeddings are deterministic
distance computations; they're closer to absentia's frequency counters
than to chat.

If we ever ship the personal-knowledge variant, embeddings will be
optional, local, and bounded to one specific feature. They won't
replace any of the structural mining.

### Natural-language query shell (LLM as input layer)

A user could reasonably want to type *"show me handlers in src/api/
that don't have @audit"* instead of writing a config block. An LLM
can translate that to a structural query the engine then runs
deterministically. The LLM is an input layer, not the engine. Same
guarantees: the output is reproducible, traceable, and explained by
the rule that fires.

This would be additive, off by default, and obviously distinct from
the core. It would not change the answer to "where do my findings
come from."

## When you'd actually want an AI tool

If you're looking for:

- *"Explain what this 500-line function does"* — use an LLM
- *"Find security vulnerabilities by understanding semantics"* — use a static analyzer + LLM hybrid
- *"Suggest a refactor"* — use Copilot
- *"Detect bugs by understanding intent"* — use the latest model

Absentia is the wrong tool for every one of those. It doesn't model
your code's intent. It doesn't reason. It counts.

But for *"surface every place my codebase diverged from a convention
it already follows,"* counting is exactly the right operation, and
counting is what you want to be deterministic, fast, free,
explainable, and verifiable.

That's the deal absentia offers. Read the [how-mining-works
explanation](how-mining-works.md) for the mechanics, or the
[what-is-negative-space explanation](what-is-negative-space.md) for
the framing.
