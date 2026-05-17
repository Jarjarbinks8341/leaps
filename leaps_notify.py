# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "yfinance>=0.2",
#   "pandas>=2.0",
#   "numpy>=1.26",
#   "pyarrow>=14.0",
# ]
# ///
"""Daily QQQ LEAPS signal check, position monitoring, and email notification.

Usage:
    uv run leaps_notify.py                     # check signal, value positions, send email
    LEAPS_NAV=50000 uv run leaps_notify.py     # custom account size for lot sizing

Open positions are tracked in positions.json. Edit that file manually when you
buy or sell. The script reads it, computes current valuations, and flags exits.
"""
import json
import os
import smtplib
import ssl
import sys
from datetime import date, timedelta
from email.mime.text import MIMEText
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from strategy.data import load
from strategy.options import call_price, realized_vol, strike_for_delta
from strategy.portfolio import _exit_reason
from strategy.signals import bullish_divergence, compute_macd, signal_strength, vix_elevated

PARAMS_FILE = "best_params_v9r_partial.json"
POSITIONS_FILE = "positions.json"
REPORT_FILE = "LEAPS_REPORT.md"


def _send_gmail(subject: str, body: str) -> bool:
    username = os.environ.get("SMTP_USERNAME", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    to = os.environ.get("NOTIFY_TO", username)
    if not (username and password):
        print(f"[email skip — no SMTP creds] {subject}")
        return False
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = username
    msg["To"] = to
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
        s.login(username, password)
        s.sendmail(username, [to], msg.as_string())
    return True


def main() -> None:
    params = json.loads(Path(PARAMS_FILE).read_text())
    positions = json.loads(Path(POSITIONS_FILE).read_text()) if Path(POSITIONS_FILE).exists() else []
    leaps_nav = float(os.environ.get("LEAPS_NAV", "100000"))

    # ── Load market data ───────────────────────────────────────────────────────
    data = load(ticker="QQQ")
    today = data.index[-1].date()
    S = float(data["qqq"].iloc[-1])
    sigma = realized_vol(data["qqq"])

    # ── Signal check ──────────────────────────────────────────────────────────
    _, _, hist = compute_macd(
        data["qqq"], params["macd_fast"], params["macd_slow"], params["macd_sig"]
    )
    warmup = params["macd_slow"] + params["div_lookback"] + 5
    global_i = len(data) - 1

    signal = False
    strength = 0.0
    signal_reason = "warmup not complete"

    if global_i >= warmup:
        p_win = data["qqq"].iloc[global_i - params["div_lookback"] : global_i + 1]
        h_win = hist.iloc[global_i - params["div_lookback"] : global_i + 1]
        v_win = data["vix"].iloc[: global_i + 1]
        div = bullish_divergence(
            p_win, h_win, params["div_lookback"], params["div_min_gap"],
            neg_hist=params.get("neg_hist", True),
        )
        vix_ok = vix_elevated(v_win, params["vix_ma"])
        if div and vix_ok:
            signal = True
            strength = signal_strength(
                p_win, h_win, v_win,
                params["div_lookback"], params["div_min_gap"], params["vix_ma"],
            )
            signal_reason = f"MACD divergence + VIX elevated  (strength {strength:.2f})"
        else:
            parts = []
            if not div:
                parts.append("no MACD divergence")
            if not vix_ok:
                parts.append("VIX not elevated")
            signal_reason = " | ".join(parts)

    # ── Buy recommendation ────────────────────────────────────────────────────
    buy_rec: dict = {}
    if signal:
        T = params["dte_days"] / 365.0
        K = strike_for_delta(S, T, sigma, params["target_delta"])
        premium = call_price(S, K, T, sigma)
        lo = params["lot_pct"]
        hi = params["lot_pct_max"]
        actual_pct = lo + (hi - lo) * strength
        contracts = max(1, int(leaps_nav * actual_pct / (premium * 100)))
        buy_rec = {
            "strike": K,
            "premium": premium,
            "expiry": (today + timedelta(days=params["dte_days"])).isoformat(),
            "contracts": contracts,
            "cost": contracts * 100 * premium,
            "lot_pct": actual_pct,
        }

    # ── Value open positions + check exits ────────────────────────────────────
    pos_rows: list[dict] = []
    exit_alerts: list[tuple] = []

    for pos in positions:
        entry_date = date.fromisoformat(pos["entry_date"])
        expiry_date = date.fromisoformat(pos["expiry_date"])
        tte = max((expiry_date - today).days, 0) / 365.0
        cur_prem = call_price(S, pos["strike"], tte, sigma)
        pnl_pct = (cur_prem - pos["entry_premium"]) / pos["entry_premium"]
        months_held = (today - entry_date).days / 30.44
        months_to_expiry = (expiry_date - today).days / 30.44
        cost = pos["entry_premium"] * pos["contracts"] * 100
        cur_value = cur_prem * pos["contracts"] * 100

        exit_r = _exit_reason(months_held, pnl_pct, params, pos.get("used_tiers", []))
        if not exit_r and months_to_expiry < params.get("min_months_remaining", 4):
            exit_r = "dte"

        row = {
            **pos,
            "cur_prem": cur_prem,
            "pnl_pct": pnl_pct,
            "pnl_usd": cur_value - cost,
            "cost": cost,
            "cur_value": cur_value,
            "months_held": months_held,
            "months_to_expiry": months_to_expiry,
            "exit_reason": exit_r,
        }
        pos_rows.append(row)
        if exit_r:
            exit_alerts.append((pos, exit_r, pnl_pct))

    total_cost  = sum(r["cost"] for r in pos_rows)
    total_value = sum(r["cur_value"] for r in pos_rows)
    total_pnl   = total_value - total_cost

    # ── Build report ──────────────────────────────────────────────────────────
    SEP = "─" * 60
    lines: list[str] = []

    lines += [
        f"QQQ LEAPS Daily Report  {today}",
        SEP,
        f"  QQQ    ${S:>10,.2f}",
        f"  Vol    {sigma:>10.1%}  (30-day realized)",
        f"  NAV    ${leaps_nav:>10,.0f}  (LEAPS_NAV budget)",
        SEP,
    ]

    # Signal
    lines.append("")
    if signal:
        lines += [
            f"  SIGNAL: BUY  —  {signal_reason}",
            "",
            f"  Recommended trade:",
            f"    Expiry   {buy_rec['expiry']}  (~{params['dte_days']} DTE)",
            f"    Strike   ${buy_rec['strike']:,.1f}  (delta ~{params['target_delta']})",
            f"    Premium  ~${buy_rec['premium']:,.2f}",
            f"    Lots     {buy_rec['contracts']} contracts  (lot_pct {buy_rec['lot_pct']:.0%})",
            f"    Cost     ~${buy_rec['cost']:,.0f}",
            "",
            f"  After filling, add to positions.json:",
            f'  {{"entry_date":"{today}","expiry_date":"{buy_rec["expiry"]}",'
            f'"strike":{buy_rec["strike"]:.1f},"entry_premium":<ACTUAL_FILL>,'
            f'"contracts":{buy_rec["contracts"]}}}',
        ]
    else:
        lines.append(f"  SIGNAL: WAIT  —  {signal_reason}")

    # Exit alerts
    if exit_alerts:
        lines += ["", SEP, f"  EXIT ALERTS ({len(exit_alerts)})"]
        for pos, reason, pnl_pct in exit_alerts:
            sign = "+" if pnl_pct >= 0 else ""
            lines.append(
                f"  [{reason.upper():>5}]  entry {pos['entry_date']}  "
                f"strike ${pos['strike']:.1f}  {pos['contracts']}c  "
                f"P&L {sign}{pnl_pct:.1%}"
            )

    # Positions table
    lines += ["", SEP, f"  Open Positions ({len(pos_rows)})"]
    if pos_rows:
        lines += [
            f"  Total cost ${total_cost:,.0f}  |  "
            f"Value ${total_value:,.0f}  |  "
            f"P&L {'+'if total_pnl>=0 else ''}${total_pnl:,.0f}",
            "",
            f"  {'Entry':<12} {'Expiry':<12} {'Strike':>8} {'C':>3} "
            f"{'Entry$':>7} {'Cur$':>7} {'P&L':>7} {'Held':>6} {'DTE':>5}  Status",
            f"  {'─'*12} {'─'*12} {'─'*8} {'─'*3} "
            f"{'─'*7} {'─'*7} {'─'*7} {'─'*6} {'─'*5}  {'─'*6}",
        ]
        for r in pos_rows:
            sign = "+" if r["pnl_pct"] >= 0 else ""
            status = f"EXIT-{r['exit_reason'].upper()}" if r["exit_reason"] else "hold"
            lines.append(
                f"  {r['entry_date']:<12} {r['expiry_date']:<12} "
                f"${r['strike']:>7.1f} {r['contracts']:>3} "
                f"${r['entry_premium']:>6.2f} ${r['cur_prem']:>6.2f} "
                f"{sign}{r['pnl_pct']:>6.1%} {r['months_held']:>5.1f}m "
                f"{r['months_to_expiry']:>4.1f}m  {status}"
            )
    else:
        lines.append("  No open positions. Edit positions.json to add entries.")

    lines += ["", SEP, f"  Params: {PARAMS_FILE}"]

    report = "\n".join(lines)

    # ── Write report file ─────────────────────────────────────────────────────
    Path(REPORT_FILE).write_text(report + "\n")
    print(report)

    # ── Email subject + send ──────────────────────────────────────────────────
    tags: list[str] = []
    if signal:
        tags.append("BUY SIGNAL")
    if exit_alerts:
        reasons_str = "/".join(sorted({r for _, r, _ in exit_alerts})).upper()
        tags.append(f"{len(exit_alerts)} EXIT ({reasons_str})")
    if not tags:
        tags.append(f"{len(pos_rows)} open" if pos_rows else "HOLD — no positions")

    subject = f"[LEAPS] {' + '.join(tags)} — QQQ ${S:,.0f} | {today}"
    sent = _send_gmail(subject, report)
    if sent:
        print(f"\nEmail sent: {subject}")


if __name__ == "__main__":
    main()
