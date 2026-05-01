# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "yfinance>=0.2.40",
#     "pandas>=2.0",
#     "numpy>=1.24",
#     "pandas_market_calendars>=4.4",
# ]
# ///
"""Multi-ticker LEAPS daily entry-timing signal.

Strategy (from research note, 2026-04):
  BUY when all hold, else WAIT —
    1. Price ≥10% off 52w high OR trading at/below MA200 (cheap price)
    2. ATM 30-day IV percentile < 25% over trailing 1y (cheap options)
    3. No relevant earnings within 14 days

Target contract: furthest-dated call with delta ≈ 0.80 (BSM-computed from
chain IV). Roll when DTE < ~180 to avoid theta acceleration.

IV percentile requires accumulated history. First ~20 runs per ticker will
show "bootstrapping" — ATM IV30 is stored daily in `asset_daily` so the
percentile becomes meaningful as history grows.
"""

from __future__ import annotations

import os
import smtplib
import sqlite3
import ssl
import warnings
from dataclasses import dataclass
from datetime import date, datetime
from email.mime.text import MIMEText
from math import erf, exp, isnan, log, sqrt

import numpy as np
import pandas as pd
import pandas_market_calendars as mcal
import yfinance as yf

warnings.filterwarnings("ignore")

DB_PATH = "stock_prices.db"
TICKERS = ["QQQ", "AAPL", "MSFT", "BRK-B"]
MEGA_CAPS = ["MSFT", "AAPL", "NVDA", "GOOGL", "AMZN", "META"]
# Earnings tickers to check per asset. Empty list = skip earnings check.
EARNINGS_SOURCES: dict[str, list[str]] = {
    "QQQ":   MEGA_CAPS,
    "AAPL":  ["AAPL"],
    "MSFT":  ["MSFT"],
    "BRK-B": [],        # Berkshire earnings not reliably in yfinance
}
EARNINGS_BLACKOUT_DAYS = 14
RISK_FREE_RATE = 0.045
DIVIDEND_YIELD = 0.005
TARGET_DELTA = 0.80
IV_TARGET_DTE = 30
IV_PERCENTILE_LOOKBACK = 252
IV_PERCENTILE_MIN_HISTORY = 20
RSI_ENTRY_MAX = 50


def send_gmail(subject: str, body: str) -> bool:
    """Send plain-text email via Gmail SMTP. Skips silently if creds absent."""
    user = os.environ.get("SMTP_USERNAME")
    pw = os.environ.get("SMTP_PASSWORD")
    to = os.environ.get("NOTIFY_TO") or user
    missing = [n for n, v in [("SMTP_USERNAME", user), ("SMTP_PASSWORD", pw), ("NOTIFY_TO", to)] if not v]
    if missing:
        print(f"Email skipped — missing env vars: {', '.join(missing)}")
        return False
    msg = MIMEText(body.replace('\xa0', ' '), 'plain', 'utf-8')
    msg["Subject"] = subject
    msg["From"] = f"LEAPS Bot <{user}>"
    msg["To"] = to
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
        s.login(user, pw)
        s.send_message(msg)
    return True


def nyse_open_today() -> bool:
    today = pd.Timestamp.now(tz="America/New_York").normalize().tz_localize(None)
    valid = mcal.get_calendar("NYSE").valid_days(start_date=today, end_date=today)
    return len(valid) > 0


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def call_delta(S: float, K: float, T: float, sigma: float,
               r: float = RISK_FREE_RATE, q: float = DIVIDEND_YIELD) -> float:
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 1.0 if S > K else 0.0
    d1 = (log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * sqrt(T))
    return exp(-q * T) * _norm_cdf(d1)


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS asset_daily (
            ticker TEXT NOT NULL,
            date   TEXT NOT NULL,
            close  REAL NOT NULL,
            iv30   REAL,
            PRIMARY KEY (ticker, date)
        )
        """
    )
    # Migrate legacy qqq_daily → asset_daily
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "qqq_daily" in tables:
        conn.execute(
            "INSERT OR IGNORE INTO asset_daily (ticker, date, close, iv30) "
            "SELECT 'QQQ', date, close, iv30 FROM qqq_daily"
        )
        conn.execute("DROP TABLE qqq_daily")
    conn.commit()


def upsert_daily(conn: sqlite3.Connection, ticker: str,
                 hist: pd.DataFrame, today_iv30: float | None) -> None:
    rows = [(ticker, d.date().isoformat(), float(row["Close"]), None)
            for d, row in hist.iterrows()]
    conn.executemany(
        "INSERT INTO asset_daily(ticker, date, close, iv30) VALUES (?,?,?,?) "
        "ON CONFLICT(ticker, date) DO UPDATE SET close=excluded.close",
        rows,
    )
    if today_iv30 is not None:
        today_iso = date.today().isoformat()
        last_close = float(hist["Close"].iloc[-1])
        conn.execute(
            "INSERT INTO asset_daily(ticker, date, close, iv30) VALUES (?,?,?,?) "
            "ON CONFLICT(ticker, date) DO UPDATE SET iv30=excluded.iv30",
            (ticker, today_iso, last_close, today_iv30),
        )
    conn.commit()


def compute_rsi(series: pd.Series, window: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def nearest_expiration(exps: list[str], target_days: int) -> str:
    today = pd.Timestamp.today().normalize()
    return min(exps, key=lambda e: abs((pd.to_datetime(e) - today).days - target_days))


def atm_iv30(asset: yf.Ticker, spot: float) -> tuple[float | None, str | None]:
    exps = list(asset.options or [])
    if not exps:
        return None, None
    exp = nearest_expiration(exps, IV_TARGET_DTE)
    try:
        chain = asset.option_chain(exp)
    except Exception:
        return None, None
    calls = chain.calls.copy()
    if calls.empty:
        return None, None
    calls = calls[calls["impliedVolatility"] > 0]
    if calls.empty:
        return None, None
    atm = calls.iloc[(calls["strike"] - spot).abs().argmin()]
    return float(atm["impliedVolatility"]), exp


def iv_percentile(conn: sqlite3.Connection, ticker: str,
                  current_iv: float) -> tuple[float | None, int]:
    df = pd.read_sql_query(
        "SELECT iv30 FROM asset_daily WHERE ticker=? AND iv30 IS NOT NULL "
        "ORDER BY date DESC LIMIT ?",
        conn, params=(ticker, IV_PERCENTILE_LOOKBACK),
    )
    series = df["iv30"].dropna()
    if len(series) < IV_PERCENTILE_MIN_HISTORY:
        return None, len(series)
    rank = (series < current_iv).mean() * 100
    return float(rank), len(series)


@dataclass
class LeapsCandidate:
    expiration: str
    dte: int
    strike: float
    mid: float
    bid: float
    ask: float
    delta: float
    iv: float
    intrinsic: float
    extrinsic: float
    annual_cost_pct: float


def find_leaps_candidate(asset: yf.Ticker, spot: float) -> LeapsCandidate | None:
    exps = list(asset.options or [])
    if not exps:
        return None
    latest = max(exps, key=lambda e: pd.to_datetime(e))
    try:
        calls = asset.option_chain(latest).calls.copy()
    except Exception:
        return None
    if calls.empty:
        return None
    dte = (pd.to_datetime(latest) - pd.Timestamp.today().normalize()).days
    T = dte / 365.25
    calls["delta"] = calls.apply(
        lambda r: call_delta(spot, float(r["strike"]), T,
                             float(r["impliedVolatility"] or 0.0)),
        axis=1,
    )
    calls["mid"] = (calls["bid"] + calls["ask"]) / 2
    calls = calls[(calls["delta"] > 0) & (calls["delta"] < 1) & (calls["mid"] > 0)]
    if calls.empty:
        return None
    pick = calls.iloc[(calls["delta"] - TARGET_DELTA).abs().argmin()]
    intrinsic = max(spot - float(pick["strike"]), 0.0)
    extrinsic = float(pick["mid"]) - intrinsic
    annual_cost = (extrinsic / spot) / (dte / 365.25) * 100 if dte > 0 else float("nan")
    return LeapsCandidate(
        expiration=latest,
        dte=dte,
        strike=float(pick["strike"]),
        mid=float(pick["mid"]),
        bid=float(pick["bid"]),
        ask=float(pick["ask"]),
        delta=float(pick["delta"]),
        iv=float(pick["impliedVolatility"]),
        intrinsic=intrinsic,
        extrinsic=extrinsic,
        annual_cost_pct=annual_cost,
    )


def upcoming_earnings(tickers: list[str],
                      days: int = EARNINGS_BLACKOUT_DAYS) -> list[tuple[str, str]]:
    if not tickers:
        return []
    today = pd.Timestamp.today().normalize()
    horizon = today + pd.Timedelta(days=days)
    hits: list[tuple[str, str]] = []
    for t in tickers:
        try:
            df = yf.Ticker(t).get_earnings_dates(limit=4)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        dates = pd.to_datetime(df.index).tz_localize(None)
        for d in dates:
            if today <= d <= horizon:
                hits.append((t, d.date().isoformat()))
                break
    return hits


def build_report(ticker: str,
                 price: float, ma50: float, ma200: float, rsi: float,
                 high_52w: float, drawdown_pct: float,
                 iv30: float | None, iv30_exp: str | None,
                 iv_pct: float | None, iv_hist_n: int,
                 earnings: list[tuple[str, str]],
                 leaps: LeapsCandidate | None,
                 earnings_sources: list[str]) -> tuple[str, str]:
    checks: list[tuple[str, bool]] = []
    drawdown_ok = drawdown_pct <= -10
    ma200_ok = price <= ma200 * 1.02
    price_ok = drawdown_ok or ma200_ok
    iv_ok = iv_pct is not None and iv_pct < 25
    earnings_ok = not earnings
    rsi_ok = not isnan(rsi) and rsi < RSI_ENTRY_MAX

    checks.append((
        f"Price cheap: drawdown {drawdown_pct:+.1f}% (need <=-10%) "
        f"OR price ${price:.2f} <= MA200x1.02 ${ma200 * 1.02:.2f}",
        price_ok,
    ))
    if iv_pct is None:
        checks.append((
            f"IV percentile: bootstrapping ({iv_hist_n}/{IV_PERCENTILE_MIN_HISTORY} days stored)",
            False,
        ))
    else:
        checks.append((f"IV30 percentile {iv_pct:.0f}% < 25 (n={iv_hist_n})", iv_ok))

    if earnings_sources:
        if earnings:
            ev = ", ".join(f"{t} {d}" for t, d in earnings)
            checks.append((f"No earnings in {EARNINGS_BLACKOUT_DAYS}d -- blocked by {ev}", False))
        else:
            checks.append((f"No earnings in {EARNINGS_BLACKOUT_DAYS}d", True))

    checks.append((f"RSI14 {rsi:.1f} < {RSI_ENTRY_MAX} (momentum not extended)", rsi_ok))

    verdict = "BUY" if (price_ok and iv_ok and earnings_ok and rsi_ok) else "WAIT"

    today = datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"# {ticker} LEAPS Signal -- {today}",
        "",
        f"**Verdict: {verdict}**",
        "",
        "## Market snapshot",
        f"- Price: ${price:.2f}  |  MA50: ${ma50:.2f}  |  MA200: ${ma200:.2f}  |  RSI14: {rsi:.1f}",
        f"- 52w high: ${high_52w:.2f}  |  Drawdown: {drawdown_pct:+.1f}%",
    ]
    if iv30 is not None:
        lines.append(f"- ATM IV30 ({iv30_exp}): {iv30:.1%}")
    else:
        lines.append("- ATM IV30: unavailable")

    lines += ["", "## Entry checklist"]
    for text, passed in checks:
        lines.append(f"- {'PASS' if passed else 'FAIL'} -- {text}")

    if leaps is not None:
        lines += [
            "",
            f"## LEAPS candidate (delta ~= {TARGET_DELTA:.2f})",
            f"- Expiration: {leaps.expiration} ({leaps.dte} DTE)",
            f"- Strike: ${leaps.strike:.0f}  |  Delta: {leaps.delta:.2f}  |  IV: {leaps.iv:.1%}",
            f"- Quote: bid ${leaps.bid:.2f} / ask ${leaps.ask:.2f} / mid ${leaps.mid:.2f}",
            f"- Intrinsic ${leaps.intrinsic:.2f} + Extrinsic ${leaps.extrinsic:.2f}",
            f"- Annualized extrinsic cost: {leaps.annual_cost_pct:.2f}%",
        ]
    else:
        lines += ["", "## LEAPS candidate: unavailable (no option chain returned)"]

    return "\n".join(lines) + "\n", verdict


def main() -> None:
    if not nyse_open_today():
        today_ny = pd.Timestamp.now(tz="America/New_York").date().isoformat()
        print(f"NYSE closed {today_ny} -- skipping signal run.")
        return

    today_iso = date.today().isoformat()
    notify_addr = os.environ.get("NOTIFY_TO") or os.environ.get("SMTP_USERNAME")

    conn = sqlite3.connect(DB_PATH)
    sections: list[str] = []
    try:
        ensure_schema(conn)
        for ticker in TICKERS:
            asset = yf.Ticker(ticker)
            hist = asset.history(period="2y", auto_adjust=False)
            if hist.empty:
                print(f"[{ticker}] No price history -- skipping")
                continue

            hist["MA50"] = hist["Close"].rolling(50).mean()
            hist["MA200"] = hist["Close"].rolling(200).mean()
            hist["RSI"] = compute_rsi(hist["Close"])

            latest = hist.iloc[-1]
            price = float(latest["Close"])
            ma50 = float(latest["MA50"]) if pd.notna(latest["MA50"]) else float("nan")
            ma200 = float(latest["MA200"]) if pd.notna(latest["MA200"]) else float("nan")
            rsi = float(latest["RSI"]) if pd.notna(latest["RSI"]) else float("nan")
            high_52w = float(hist["Close"].tail(252).max())
            drawdown_pct = (price / high_52w - 1) * 100

            iv30, iv30_exp = atm_iv30(asset, price)
            upsert_daily(conn, ticker, hist, iv30)
            iv_pct, iv_hist_n = (
                iv_percentile(conn, ticker, iv30) if iv30 is not None else (None, 0)
            )

            earnings_sources = EARNINGS_SOURCES.get(ticker, [])
            earnings = upcoming_earnings(earnings_sources)
            leaps = find_leaps_candidate(asset, price)

            section, verdict = build_report(
                ticker, price, ma50, ma200, rsi, high_52w, drawdown_pct,
                iv30, iv30_exp, iv_pct, iv_hist_n, earnings, leaps,
                earnings_sources=earnings_sources,
            )
            sections.append(section)
            print(section)

            if verdict == "BUY":
                subject = (
                    f"[LEAPS] BUY {ticker} {today_iso}"
                    f" - ${price:.2f} / drawdown {drawdown_pct:+.1f}%"
                )
                if send_gmail(subject, section):
                    print(f"[{ticker}] BUY email sent to {notify_addr}")
    finally:
        conn.close()

    report = "\n---\n\n".join(sections)
    with open("DAILY_REPORT.md", "w") as f:
        f.write(report)


if __name__ == "__main__":
    main()
