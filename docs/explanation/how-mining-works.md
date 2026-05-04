# How mining works

> _Stub. To be written before any v1.0 push — the trust-builder doc. Tracked in `DEFERRALS.md`._

This is the doc that earns reader trust. A user who understands "9/10 group members share feature F, this one doesn't" will trust lacuna's output. A user who thinks it's magic will second-guess every gap.

Outline:
- The four stages: Parse → Group → Mine → Compare
- Selectors as functions from entities to groups, with examples of each built-in
- Features as structural facts, with examples per kind (boolean, set, sequence, pair)
- Frequency analysis: support_n / support_total = confidence
- Compound rules via frequent itemset mining (FP-growth), gated by `max_predicate_size`
- Gap = entity in group whose feature set doesn't satisfy the rule's predicate
- Why every result has provenance: the rule is itself the explanation
- Worked example end-to-end on a small example codebase
