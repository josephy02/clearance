# Notices

clearance builds on three open-source projects. Each was cloned, its LICENSE confirmed,
and its code read before anything was reused.

| Project | License | Commit reviewed | What we use |
|---|---|---|---|
| [advayiscoding/prior-auth-completeness-gate](https://github.com/advayiscoding/prior-auth-completeness-gate) | MIT, © 2026 Advay Bhargava | `6eb84f7` | NPI Luhn check-digit rule, the verbatim-quote grounding guard, parse-or-abstain handling of model replies, and the offline keyword judge. Ported into `clearance/engine/` and generalized from hardcoded policies to criteria packs. |
| [Kartik-Hirijaganer/X12-Parser-Encoder](https://github.com/Kartik-Hirijaganer/X12-Parser-Encoder) | MIT, © 2026 Kartik Hirijaganer | `a401e45` | The AAA reject-reason message table. `clearance/integrations/x12_decode.py` is a small 271 reader written for this project; the full library was not vendored. |
| [analytiq-hub/doc-router](https://github.com/analytiq-hub/doc-router) | Apache 2.0 | `145000e` | Not used in v0.1. The spec's 3-hour timebox for running it as a sidecar was not met in this environment (no Docker), so the fallback extractor in `clearance/extract/` is the primary path. The `Extractor` interface is where a doc-router client would slot in. |

Policy text quoted in `packs/` comes from public CMS Local Coverage Determinations and DME MAC
documentation checklists (U.S. government works / public guidance).
