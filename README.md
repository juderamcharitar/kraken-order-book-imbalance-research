# Kraken BTC/USD Order-Book Imbalance Research

**Jude Kriel Ramcharitar — Sole Creator and Author**  
Independent quantitative research · November 2026 · Exploratory working paper

[Working paper (PDF)](paper/WORKING_PAPER.pdf) · [Markdown source](paper/WORKING_PAPER.md) · [Figures](figures/) · [Methods and verification notes](docs/METHODS_AUDIT.md)

This research studies displayed depth imbalance and future midprice response in one 24-hour Kraken BTC/USD Level 2 recording from 3–4 November 2026. It is a **single-day exploratory result**; it does not claim causal market impact, profitable execution, independent replication, or peer review.

## Headline results

- **33** independently initialized recording segments and **12,973,391** book-update messages (according to the recording's archived outputs); **86,057** observed one-second order-book states.
- Pooled correlation between 10-level depth imbalance and five-second future midpoint return: **+0.3113**.
- Chronological 70/30 imbalance-only test R²: **+0.0829**, relative to the training-mean constant forecast.
- Train-calibrated test imbalance D10–D1 mean five-second return difference: **+1.9160 bps**. This is a midpoint-response difference, **not** trading P&L.
- One documented 1.640-second local receive-time reversal; affected forward-label intervals excluded.

## Source of results and important limitations

The packaged `results/*.json` and CSV files are the author's archived output summaries. On 4 November 2026, the author **reran the four bundled analysis scripts against the locally retained original recording and recovered segments**; the terminal reports matched the archived summary statistics in all four phases. See [`docs/LOCAL_REPRODUCIBILITY_2026-11-04.md`](docs/LOCAL_REPRODUCIBILITY_2026-11-04.md) for the checks and their scope. The accompanying methods/code review did not independently rerun the full raw-data pipeline. Raw WebSocket recordings, the original session manifest, the derived one-second features CSV, and checksum-auditing scripts are **not included** here. Accordingly, the public package is **not a standalone independent reproduction**. Exchange checksum status comes from the prior local terminal audits.

## Files

- `paper/` — research manuscript (Markdown and PDF).
- `figures/` — plots of the archived outputs.
- `src/` — the original four Phase 1–4 analysis scripts.
- `results/` — the archived summaries and sampled tables supplied in the research package.
- `docs/METHODS_AUDIT.md` — code review scope, verification performed, limitations, and path to stronger evidence.

## Local reproduction (requires original recordings)

Use Python 3.11+ (Phase 1–4 rely only on the standard library). You must obtain the original 33 WebSocket recordings, recovered `.recovered.jsonl.gz` files, and matching session `manifest.json` for Phase 1; a historical WebSocket session cannot simply be recreated by downloading current market data.

```powershell
py .\src\kraken_l2_phase1_features.py --manifest .\data\session\manifest.json --outdir .\research_phase1
py .\src\kraken_l2_phase2_robustness.py --features .\research_phase1\one_second_l2_features.csv --outdir .\research_phase2
py .\src\kraken_l2_phase3_incremental_validation.py --features .\research_phase1\one_second_l2_features.csv --outdir .\research_phase3
py .\src\kraken_l2_phase4_statistical_stress.py --features .\research_phase1\one_second_l2_features.csv --outdir .\research_phase4
```

Original raw data are not bundled; check Kraken's applicable redistribution terms before distributing recordings. This research is not investment advice.
