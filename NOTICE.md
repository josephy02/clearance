# Notices

clearance builds on three open-source projects. Each was cloned, its LICENSE confirmed,
and its code read before anything was reused.

| Project | License | Commit reviewed | What we use |
|---|---|---|---|
| [advayiscoding/prior-auth-completeness-gate](https://github.com/advayiscoding/prior-auth-completeness-gate) | MIT, © 2026 Advay Bhargava | `6eb84f7` | NPI Luhn check-digit rule, the verbatim-quote grounding guard, parse-or-abstain handling of model replies, and the offline keyword judge. Ported into `clearance/engine/` and generalized from hardcoded policies to criteria packs. |
| [Kartik-Hirijaganer/X12-Parser-Encoder](https://github.com/Kartik-Hirijaganer/X12-Parser-Encoder) | MIT, © 2026 Kartik Hirijaganer | `a401e45` | Reviewed as the reference for 271 parsing; no code copied. Its AAA table is payer-specific (DC Medicaid, 5 codes), so `clearance/integrations/x12_decode.py` is a small 271 reader written for this project, with AAA and EB codes from the public X12 005010X279A1 code lists. The library can replace it behind the same `read_271` call. |
| [analytiq-hub/doc-router](https://github.com/analytiq-hub/doc-router) | Apache 2.0 | `145000e` | Not used in v0.1: this machine has no Docker, so the spec's fallback extractor is the primary path (local OCR + pack labels, cross-checked by Claude vision). The `Extractor` interface in `clearance/extract/` is where a doc-router client would slot in. |

Policy text quoted in `packs/` comes from public CMS Local Coverage Determinations and DME MAC
documentation checklists (U.S. government works / public guidance).
