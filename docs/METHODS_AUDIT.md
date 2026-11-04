# Methods audit — Kraken BTC/USD order-book imbalance research

**Scope:** This note describes the review of the four Python files and seven archived outputs uploaded with the research package on 4 November 2026. It does not claim external scientific review, a full replay from raw WebSocket files, or a full security audit.

## Subsequent author-run local reproduction

After the initial package/code review, on 4 November 2026 the author reran all four packaged scripts on the original local Kraken recordings and recovered `.partial` segments. The terminal summaries reproduced the archived values (including all 33 segments, 12,973,391 updates, 86,057 feature seconds, the five-second correlation and R² figures, and Phase 4 bootstrap/placebo/decile figures). See [`LOCAL_REPRODUCIBILITY_2026-11-04.md`](LOCAL_REPRODUCIBILITY_2026-11-04.md). This is **local author-run reproducibility**, not external replication, a fresh-machine dependency check, or full public reproducibility without the private raw recordings.

## Verification performed

- Confirmed all four Phase 1–4 source scripts compile successfully with Python.
- Reviewed their definitions, feature timestamping, segment isolation, warning handling, chronological train/test fitting, bootstrap and placebo logic.
- Ran small synthetic fixture checks of book feature arithmetic, ordered book changes, segment-safe exact-second target construction, chronology eligibility, and anomaly exclusion. These checks passed.
- Cross-checked reported 5-second correlation, model R², negative-control, decile spread, and within-day resampling numbers against the uploaded archived JSON/CSV outputs.
- Checked readable scripts, summary JSON, and CSV tables for obvious embedded home-directory paths, email addresses, and literal API-key patterns; no such strings were identified. This is a targeted check, not a comprehensive security audit.

## Findings and required interpretation

1. **The original code review used archived outputs, not a new end-to-end replay.** A subsequent author-run replay on the original recording reproduced the reported summaries. Raw recordings, recovery products, original manifest and one-second feature CSV are not in this public release archive.
2. **Exchange checksum status derives from the prior terminal outputs.** This audit did not rerun CRC32 against raw messages. The Phase 1 script does not do so by itself.
3. **One receive-time regression was observed.** Segment 5's local receipt clock moved backward approximately 1.640 seconds. Phase 1 clamps the observed second and flags nearby seconds; downstream samples discard warning-crossing labels. Remaining event-time synchronization limitations persist.
4. **Strong serial dependence.** Outcomes over 5, 10, 30 and 60 seconds often overlap. Five-minute blocks remain adjacent and may be dependent. The reported bootstrap intervals and 300-permutation p-value are conditional within-day diagnostics.
5. **Selection after inspection.** The five-second horizon was highlighted after viewing the sample, so within-day significance statistics are not independent confirmation.
6. **The later 30% is not an untouched dataset.** Models are fitted only to training rows, but model family and horizon selection were informed by the same day's broader exploratory observations.
7. **Correctly named predictor.** The predictor is 10-level depth imbalance, not the event-based order-flow-imbalance estimator of Cont, Kukanov and Stoikov (2014).
8. **No executable impact measurement.** The return is based on midquotes; the static book-walk cost omits latency, adverse selection, partial fill, queue dynamics and fees. No trade execution, market order identifier, or causal design appears in the source data.
9. **Narrow baselines.** Momentum and microprice controls do not exhaust realistic market microstructure comparators; 5-second test R² is against the training-mean constant predictor.
10. **Regression stabilization.** Phase 3's `fit_ols` adds a tiny diagonal term (1e-9 times the number of training observations after standardization). It should be described as a numerically stabilized near-OLS linear model rather than exactly unpenalized OLS.
11. **Reproducibility boundaries.** Segments are reset on reconnect, exact-second target endpoints are used, and no forward labels cross segment boundaries. These were confirmed by source review and small synthetic tests, not by replaying source messages.
12. **Technical debt, not a publication blocker:** add a reproducible source-manifest and feature-file versioned artifact, immutable source checksums, a fresh-machine run, deterministic CI tests, and a newer untouched recording with the horizon fixed before looking at outcomes.

## Recommended next validation protocol

Before collecting a second day, freeze the exact five-second outcome, 10-level depth imbalance definition, period-end availability rule, evaluation timestamp policy, 70/30 or walk-forward calibration method, block length and comparator models. Preserve snapshots and CRC32 manifests on every reconnect. Obtain a second day without revising the selected horizon after seeing results. Report day-level estimates alongside the exploratory Nov 3–4 result. This is the clearest way to distinguish an interesting within-day observation from a reusable predictive regularity.
