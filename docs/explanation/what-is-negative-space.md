# What is negative-space search?

Absentia finds the holes your codebase already drew — places where one piece of code drifted away from a pattern the rest of the codebase follows. The pattern doesn't have to be written anywhere. If nine functions in a folder share a decorator and the tenth doesn't, absentia will tell you about the tenth.

This doc explains why that's worth doing, and why no other tool quite does it.

## The two questions other tools answer

Most code-quality tools answer one of two questions:

**"Did you violate a rule someone wrote?"**
Linters and style checkers — `ruff`, ESLint, gofmt, Prettier. Someone — you, your team, a published style guide — decided that imports should be sorted, that lines should wrap at 100 chars, that `if` should have a space after it. The tool checks whether your code respects those rules. *The rules come from a config file or a public standard.*

**"Is this code likely buggy?"**
Static analyzers — `mypy`, `pyright`, `sonarqube`, security-focused semgrep. The tool encodes patterns that *cause bugs in general* and looks for them in your code: null derefs, type confusion, SQL injection, race conditions. *The rules come from compiler theory, security research, and decades of post-mortems.*

Both are useful. Both have their tools. Neither answers the question that actually keeps codebases consistent over years.

## The third question

**"Does this code follow the patterns the rest of *this* codebase follows?"**

Most code drift isn't bugs and isn't style violations. It's a piece that diverged from a convention nobody wrote down. Some real examples:

- Every API endpoint logs `user_id` at the top — except this one
- Every panel inherits `BasePanel` — except this one
- Every test file has a corresponding source file — except this one (orphaned)
- Every config field has a default — except this new one (will explode in prod)
- Every error type extends `AppError` — except this one (will get caught by the wrong handler)

None of these are bugs in the type-error sense. Mypy is happy. Pyright is happy. The tests pass. None are style violations either — ruff has no opinion about which decorators you use.

But they *are* the kind of thing that, when a teammate skims the diff, makes them say *"wait, why doesn't this one have…?"* — and either it's intentional and worth a comment, or it's an oversight and should be fixed.

Absentia's job is to find these before that teammate has to.

## Why "negative space"

In photography and design, *negative space* is what surrounds the subject — the empty area that defines the subject's shape. A photograph of a tree against a featureless sky relies on negative space; the subject is *defined* by what isn't there.

Code has negative space too. The shape of an unwritten convention is defined by absences. Nine endpoints have `@audit`, the tenth doesn't — the consistency of the absence-pattern across nine of them is what makes "uses `@audit`" a recognizable convention in the first place.

Existing tools look at code and check what's there. Absentia looks at patterns and shows you the holes in them. That's the difference.

## A worked example

Suppose your project has `src/api/users.py` with ten endpoints. Nine are wrapped with `@audit`. The tenth — `delete_user` — isn't.

```bash
$ absentia check
GAPS                                              confidence ≥ 0.80   1

  src/api/users.py:42       fn `delete_user`     missing @audit       0.90

  1 gaps  ·  1 rules
```

Now the conversation goes one of two ways:

- **"Oh — that's a real oversight. We forgot to audit deletes."** → fix the code, re-run, the gap disappears.
- **"That's intentional. `delete_user` *is* the audit endpoint itself; auditing it would recurse."** → `absentia suppress g-7c91 --reason "audit endpoint itself"`, the gap disappears with a recorded reason that future you (or your teammate) can read.

Either way, the system has done its job: it surfaced the divergence so a human could decide. It doesn't know which way is right — that's not its job.

## What absentia isn't

The value prop is unusual enough that it's easy to mis-shelve. A few clarifying disclaimers:

- **Not a linter.** Linters enforce rules someone else wrote. Absentia enforces rules your codebase already follows.
- **Not a static analyzer.** Absentia doesn't know what your code does or whether it's correct. It only notices when one piece stops looking like its siblings.
- **Not AI.** No model. No embeddings. The patterns are statistical facts about your code, computed by counting. Run absentia twice and you get the same answer; every gap traces back to a rule, and every rule traces back to the members of your code that exhibit it. See [Why no LLM](why-no-llm.md) for the longer version.
- **Not a fixer.** Absentia finds; humans fix. Auto-patching is a different product with very different tradeoffs.

## When absentia pays off

Absentia shines when your codebase has established patterns. In practice that means:

- **Each group you'd want to mine has at least ~5 members.** Three endpoints don't establish a pattern; ten do. (The default `min_group_size` is 3, but rules need real signal — a 2/3 majority is weak evidence.)
- **A team that develops local conventions.** Most teams do, even when they don't realize it.
- **Periodic hygiene sweeps** — once a sprint, before a release, when onboarding a new contributor who'll otherwise propose ten PRs that gently violate norms nobody told them about.
- **Pre-merge or CI runs** for medium-sized changes, especially when the change adds to an existing folder where conventions are already strong.

Absentia is less useful when:

- **The codebase is too small** for patterns to be statistically meaningful.
- **Heterogeneity is the goal** — a directory of one-off migration scripts, example code that intentionally varies, vendored libraries you don't own. The right answer here is sometimes *"this folder shouldn't be mined"* — `[scan].exclude` or a tighter `kind_filter` keeps absentia's attention on the parts of the tree where conventions actually exist.
- **What you actually need is a bug-finder.** Reach for mypy, pyright, ruff, semgrep — not absentia.

## The thing that ages a codebase

It's not bugs — bugs get fixed. It's drift. The third endpoint added by the third person who didn't notice the convention. The fifth helper that didn't get the test the first four had. The new config field that didn't get the default.

By the time the team realizes the convention is broken, ten more pieces have followed the broken pattern and the convention isn't really a convention anymore.

Absentia catches it on the third, not the thirteenth. That's the bet.
