# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Layout

Git repo lives in the nested `leaps/leaps/` directory. Outer `leaps/` is just the clone parent — always operate from the inner folder.

## Purpose

Single-script daily entry-timing signal for buying **QQQ LEAPS** (deep ITM long-dated calls, delta ≈ 0.80, furthest expiration). No other tickers, no other strategies.

## The Signal (qqq_leaps.py)

The script is one decision: **BUY** the LEAPS candidate today, or **WAIT**. It prints a report and writes `DAILY_REPORT.md`. Three gates must all pass to emit BUY:

1. **Price cheap** — QQQ is ≥10% off its 52w high OR trading within 2% of MA200.
2. **IV cheap** — ATM 30-day IV is below the 25th percentile of trailing stored history (up to 252 days).
3. **Earnings clear** — no mega-cap (MSFT, AAPL, NVDA, GOOGL, AMZN, META) earnings within 14 days.

Delta for the LEAPS candidate is computed via Black-Scholes (hand-rolled, no scipy) using the chain's IV, `RISK_FREE_RATE=0.045`, `DIVIDEND_YIELD=0.005`. The picker scans the furthest-dated expiration and returns the call whose BSM delta is closest to 0.80.

## NYSE Holiday Gate

The script's first action is `nyse_open_today()` (via `pandas_market_calendars`). On weekends, US federal/NYSE holidays, and any day the exchange is closed, it prints a skip message and exits 0 — no DB write, no report update, no commit. This runs *before* any yfinance calls, so a holiday run is cheap. The cron (`Mon-Fri 15:00 UTC`) already prunes weekends; the gate is what handles Good Friday, MLK, Memorial, Juneteenth, Independence, Labor, Thanksgiving, and Christmas.

## IV Percentile Bootstrap

yfinance does not expose historical IV, so the script stores today's ATM IV30 into `qqq_daily.iv30` on every run. Until ≥20 days are stored the percentile gate reports "bootstrapping" and the verdict stays WAIT. Don't remove the SQLite commit step or the persisted DB file — the history has to accumulate. CI commits `stock_prices.db` back to the repo for this reason.

## Commands

Deps are declared inline in `qqq_leaps.py` via a PEP 723 metadata block. `uv` reads that block, materializes an ephemeral env, and runs the script — no venv, no `pip install`, no `requirements.txt`.

```bash
uv run qqq_leaps.py                  # prints report, writes DAILY_REPORT.md, upserts qqq_daily
sqlite3 stock_prices.db "SELECT date, close, iv30 FROM qqq_daily ORDER BY date DESC LIMIT 20"
```

When adding a dep, edit the `# /// script ... # ///` header at the top of `qqq_leaps.py` — do not reintroduce `requirements.txt` or `pyproject.toml` unless the repo grows past one script.

## CI

`.github/workflows/daily_leaps.yml` runs Mon–Fri at 15:00 UTC via `astral-sh/setup-uv` + `uv run qqq_leaps.py`, and commits both `DAILY_REPORT.md` and `stock_prices.db` back under the `LEAPS-Bot` identity. The DB commit is what makes the IV history durable across runs.

## Tuning Knobs

All thresholds are module-level constants at the top of `qqq_leaps.py`:
`TARGET_DELTA`, `IV_TARGET_DTE`, `IV_PERCENTILE_LOOKBACK`, `IV_PERCENTILE_MIN_HISTORY`, `EARNINGS_BLACKOUT_DAYS`, `RISK_FREE_RATE`, `DIVIDEND_YIELD`, `MEGA_CAPS`. Keep changes to constants — avoid scattering magic numbers through the functions.
