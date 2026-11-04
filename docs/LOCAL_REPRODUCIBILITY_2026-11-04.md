# Local end-to-end reproducibility verification — 4 November 2026

**Source:** Console output from an author-run replay of the four publication-package scripts against the original Kraken BTC/USD order-book recording and recovered `.partial` segments. The same local computer and locally retained data were used. The author supplied the terminal transcript; reviewers of the release package **did not receive the raw recordings or independently rerun these commands**.

## Scope and procedure

- Phase 1: `src/kraken_l2_phase1_features.py` run with the original 24-hour manifest, 33 segment files (including seven recovered segment streams), and a new `phase1` output directory.
- Phase 2: `src/kraken_l2_phase2_robustness.py` run on the **newly generated** Phase 1 `one_second_l2_features.csv`.
- Phase 3: `src/kraken_l2_phase3_incremental_validation.py` run on the same newly generated CSV.
- Phase 4: `src/kraken_l2_phase4_statistical_stress.py` run on the same newly generated CSV.

## Terminal results and archived-summary consistency

| Check | Reproduced output |
|---|---|
| Phase 1 segments | 33/33 processed |
| Phase 1 book-update messages | 12,973,391 |
| Phase 1 one-second feature rows | 86,057 |
| Receive-time regressions flagged | 1 (segment 5) |
| Five-second pooled imbalance/forward-mid-return correlation | +0.3113003745 |
| Five-second within-5-minute correlation, Phase 2 | +0.3135 (printed precision) |
| Five-second chronological imbalance-only test R² | +0.0829 |
| Five-second nonoverlapping correlation | +0.3055 |
| Five-second segment signs | 31/31 positive eligible segments |
| Phase 3 current imbalance correlation | +0.3113 |
| Phase 3 correlation for 60-second-stale imbalance | -0.0018 |
| Phase 3 one-second-delayed correlation | +0.2321 |
| Phase 3 combined model chronological test R² | +0.0851 |
| Phase 3 incremental R² over momentum/microprice comparator | +0.0758 R² points |
| Phase 4 eligible five-second pairs | 85,551 |
| Phase 4 five-minute blocks | 295 |
| Phase 4 within-five-minute correlation and block-bootstrap 95% interval | +0.31355; [+0.29952, +0.32968] |
| Phase 4 time-shift placebo | observed +0.31406; two-sided p = 0.00332; 289 blocks |
| Phase 4 chronological-test D10 minus D1 mean forward midpoint return | +1.91603 bps; block-bootstrap 95% interval [+1.74139, +2.11129] bps |

The printed Phase 1–4 values matched the archived project summaries at displayed precision. This provides meaningful **same-environment local replay evidence** and rules out some packaging/path problems. It does not prove that the reported estimates are causal, independently replicated, statistically preregistered, or profitable after execution costs.

## Data-integrity evidence from preceding local audits

Earlier author-reported checks found 26/26 complete files passed gzip CRC and stored SHA-256 checks; all seven interrupted `.jsonl.gz.partial` segments were recovered, with 2,649,274 valid JSONL rows. Exchange CRC32 book validations passed on the recovered/complete streams, with a single 1.64023-second *local receive-time* reversal in segment 5. Segment boundaries were handled separately. This document describes reported validation results rather than independently reproducing those checks from raw files.

## Remaining research limits

One day's historical market states, post-exploration five-second horizon selection, overlapping returns, imperfectly independent resampling blocks, a non-preregistered chronological split, and no independently available raw feed replay. These are exploratory within-session findings and do **not** establish causal market impact, investment alpha, or independent replication.
