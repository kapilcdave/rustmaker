# FINDINGS — GLiNER2.5-Decide rule-relation triage

Date: 2026-10-06.  Preregistration:
`PREREG_gliner_rule_relation_20261006.md`.

## Verdict

Closed as degenerate.

On 80 balanced synthetic rule pairs, `fastino/GLiNER2.5-Decide` emitted
`same_event` 80/80:

- exact-label accuracy: 20%;
- `same_event` recall: 100%;
- `first_superset`, `first_subset`, and `different_settlement` recall: 0%.

The checkpoint therefore cannot triage deterministic settlement relations in
this zero-shot formulation.  It is not used by the hourly-dominance scanner.
Index, close time, averaging window, comparator, strike and published
expiration values remain code-verified fields.

Artifact: `data/cf_exact/gliner_rule_relation.json`.
