# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Layout

The git root is the `leaps/` directory you cloned into — all files live here. `qqq_leaps.py` is the only script; `stock_prices.db` is the SQLite store; `.github/workflows/daily_leaps.yml` is the CI definition.

## Purpose

Single-script daily entry-timing signal for buying **QQQ LEAPS** (deep ITM long-dated calls, delta ≈ 0.80, furthest expiration). No other tickers, no other strategies.

## The Signal (qqq_leaps.py)

The script is one decision: **BUY** the LEAPS candidate today, or **WAIT**. It prints a report and writes `DAILY_REPORT.md`. Three gates must all pass to emit BUY:

1. **Price cheap** — QQQ is ≥10% off its 52w high OR trading within 2% of MA200.
2. **IV cheap** — ATM 30-day IV is below the 25th percentile of trailing stored history (up to 252 days).
3. **Earnings clear** — no mega-cap (MSFT, AAPL, NVDA, GOOGL, AMZN, META) earnings within 14 days.

Delta for the LEAPS candidate is computed via Black-Scholes (hand-rolled, no scipy) using the chain's IV, `RISK_FREE_RATE=0.045`, `DIVIDEND_YIELD=0.005`. The picker scans the furthest-dated expiration and returns the call whose BSM delta is closest to 0.80.

## Internal Structure

All logic lives in `qqq_leaps.py` (~350 lines). Key functions in call order:

| Function | Role |
|---|---|
| `nyse_open_today()` | Gate: exits early on non-trading days via `pandas_market_calendars` |
| `upsert_daily(date, close, iv30)` | Writes today's close + IV30 to `qqq_daily` via INSERT…ON CONFLICT upsert |
| `iv_percentile(conn)` | Queries `qqq_daily`, returns percentile rank of latest iv30 over up to 252 rows |
| `bsm_call_delta(S, K, T, r, q, sigma)` | Black-Scholes delta, implemented with `math.erf` |
| `find_leaps_candidate(ticker, target_delta)` | Scans furthest-dated option chain expiration for closest delta to `target_delta` |
| `build_report(conn)` | Runs all checks, returns `(markdown_str, verdict_str)` |
| `send_gmail(subject, body)` | Sends report via SMTP SSL; silent no-op if env vars absent |
| `main()` | Entry point: NYSE gate → DB → report → email |

`LeapsCandidate` is a dataclass capturing: expiration, DTE, strike, bid/ask/mid, delta, IV, intrinsic value, extrinsic value, annualized extrinsic cost %.

The `qqq_daily` table schema: `(date TEXT PRIMARY KEY, close REAL, iv30 REAL)`.

## NYSE Holiday Gate

`nyse_open_today()` runs first in `main()`. On weekends, US federal/NYSE holidays, and any day the exchange is closed, it prints a skip message and exits 0 — no DB write, no report update, no commit. The cron (`Mon-Fri 15:00 UTC`) prunes weekends; the gate handles Good Friday, MLK, Memorial, Juneteenth, Independence, Labor, Thanksgiving, and Christmas.

## IV Percentile Bootstrap

yfinance does not expose historical IV, so the script stores today's ATM IV30 into `qqq_daily.iv30` on every run. Until ≥20 days are stored the percentile gate reports "bootstrapping" and the verdict stays WAIT. Do not remove the SQLite commit step or the persisted DB file — the history has to accumulate. CI commits `stock_prices.db` back to the repo for this reason.

The iv30 upsert uses INSERT…ON CONFLICT so that a Saturday/Sunday run (no yfinance price row for today) still creates a row using the last known close — preventing the IV history from silently failing to accumulate on non-trading-day runs.

## Commands

Deps are declared inline in `qqq_leaps.py` via a PEP 723 metadata block. `uv` reads that block, materializes an ephemeral env, and runs the script — no venv, no `pip install`, no `requirements.txt`.

```bash
uv run qqq_leaps.py                  # prints report, writes DAILY_REPORT.md, upserts qqq_daily
sqlite3 stock_prices.db "SELECT date, close, iv30 FROM qqq_daily ORDER BY date DESC LIMIT 20"
```

When adding a dep, edit the `# /// script ... # ///` header at the top of `qqq_leaps.py` — do not reintroduce `requirements.txt` or `pyproject.toml` unless the repo grows past one script.

## CI

`.github/workflows/daily_leaps.yml` runs Mon–Fri at 15:00 UTC via `astral-sh/setup-uv` + `uv run qqq_leaps.py`, and commits both `DAILY_REPORT.md` and `stock_prices.db` back under the `LEAPS-Bot` identity. The DB commit is what makes the IV history durable across runs.

## Email Notification

After writing `DAILY_REPORT.md`, `main()` sends the same report to Gmail via SMTP on port 465 when these env vars are all set:

- `SMTP_USERNAME` — sending Gmail address
- `SMTP_PASSWORD` — a Google **App Password** (not the account password; requires 2FA; generated at myaccount.google.com/apppasswords)
- `NOTIFY_TO` — recipient; falls back to `SMTP_USERNAME` if unset

If any of the three are missing, `send_gmail()` returns False silently — local runs don't spam, and the skip doesn't fail the job. In CI the values come from the `GMAIL_USERNAME`, `GMAIL_APP_PASSWORD`, and `NOTIFY_TO` repo secrets. Subject format: `[QQQ LEAPS] {BUY|WAIT} {YYYY-MM-DD} — ${price} / drawdown {pct}%`. On NYSE-closed days the NYSE gate exits before this code path, so no email goes out.

## Tuning Knobs

All thresholds are module-level constants at the top of `qqq_leaps.py`:
`TARGET_DELTA`, `IV_TARGET_DTE`, `IV_PERCENTILE_LOOKBACK`, `IV_PERCENTILE_MIN_HISTORY`, `EARNINGS_BLACKOUT_DAYS`, `RISK_FREE_RATE`, `DIVIDEND_YIELD`, `MEGA_CAPS`. Keep changes to constants — avoid scattering magic numbers through the functions.
