"""QQQ LEAPS Backtest — MACD Bullish Divergence + VIX elevation strategy.

Usage:
    uv run backtest.py                                    # default params, train period
    uv run backtest.py --start 2025-01-01                 # OOS test
    uv run backtest.py --refresh                          # re-download data
    uv run backtest.py --params best_params.json          # load params from optimizer
"""
import argparse
import json
import sys
from datetime import date

from strategy.data import load
from strategy.metrics import summary, score
from strategy.options import realized_vol
from strategy.portfolio import Portfolio
from strategy.signals import compute_macd, bullish_divergence, vix_elevated

DEFAULT_PARAMS: dict = {
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_sig": 9,
    "div_lookback": 20,
    "div_min_gap": 5,
    "vix_ma": 20,
    "target_delta": 0.60,
    "dte_days": 365,
    "max_pos": 5,
    "pos_pct": 0.05,
    "tier1_months": 4,
    "tier1_profit": 0.50,
    "tier2_months": 6,
    "tier2_profit": 0.30,
    "tier3_months": 9,
    "tier3_profit": 0.10,
    "force_months": 9,
}

INITIAL_CASH = 100_000.0


def run(
    params: dict | None = None,
    start: str = "2015-01-01",
    end: str = "2024-12-31",
    data=None,
    refresh: bool = False,
    ticker: str = "QQQ",
) -> dict:
    """Run a full backtest. Returns metrics dict including curve and trades."""
    if params is None:
        params = DEFAULT_PARAMS
    if data is None:
        data = load(refresh=refresh, ticker=ticker)

    _, _, hist = compute_macd(data["qqq"], params["macd_fast"], params["macd_slow"], params["macd_sig"])

    pf = Portfolio(INITIAL_CASH, params["max_pos"], params["pos_pct"])

    sub = data.loc[start:end]
    warmup = params["macd_slow"] + params["div_lookback"] + 5

    for i, (d, row) in enumerate(sub.iterrows()):
        global_i = data.index.get_loc(d)
        S = float(row["qqq"])
        sigma = realized_vol(data["qqq"].iloc[: global_i + 1])

        signal = False
        if global_i >= warmup:
            p_win = data["qqq"].iloc[global_i - params["div_lookback"] : global_i + 1]
            h_win = hist.iloc[global_i - params["div_lookback"] : global_i + 1]
            v_win = data["vix"].iloc[: global_i + 1]
            signal = bullish_divergence(
                p_win, h_win, params["div_lookback"], params["div_min_gap"]
            ) and vix_elevated(v_win, params["vix_ma"])

        pf.step(d, S, sigma, signal, params)

    m = summary(pf.curve, pf.trades)
    m["curve"] = pf.curve
    m["trades"] = pf.trades
    m["positions"] = pf.positions
    m["score"] = score(pf.curve, pf.trades)
    return m


def _print_report(m: dict, params: dict, start: str, end: str, show_trades: bool = False) -> None:
    print(f"\n{'─' * 52}")
    print(f"  QQQ LEAPS Backtest  {start} → {end}")
    print(f"{'─' * 52}")
    print(f"  Final Value   ${m['final_value']:>12,.0f}   (start ${INITIAL_CASH:,.0f})")
    print(f"  CAGR          {m['cagr']:>11.1%}")
    print(f"  Max Drawdown  {m['max_dd']:>11.1%}")
    print(f"  Sharpe        {m['sharpe']:>12.2f}")
    print(f"  Calmar        {m['calmar']:>12.2f}")
    print(f"  Win Rate      {m['win_rate']:>11.1%}")
    print(f"  # Trades      {m['n_trades']:>12d}")
    print(f"  Score         {m['score']:>12.4f}")
    print(f"{'─' * 52}")

    if m["trades"]:
        by_reason: dict[str, int] = {}
        for t in m["trades"]:
            by_reason[t.reason] = by_reason.get(t.reason, 0) + 1
        print("  Exit reasons:")
        for reason, cnt in sorted(by_reason.items(), key=lambda x: -x[1]):
            print(f"    {reason:<14} {cnt:>4} trades")
        print(f"{'─' * 52}")

    if show_trades and m["trades"]:
        print(f"\n  {'#':>3}  {'Entry':10}  {'Exit':10}  {'Contracts':>9}  {'Entry$':>8}  {'Exit$':>8}  {'P&L':>7}  {'Cost':>10}  Reason")
        print(f"  {'─'*3}  {'─'*10}  {'─'*10}  {'─'*9}  {'─'*8}  {'─'*8}  {'─'*7}  {'─'*10}  {'─'*6}")
        for i, t in enumerate(m["trades"], 1):
            sign = "+" if t.pnl_pct >= 0 else ""
            entry = str(t.entry_date)[:10]
            exit_ = str(t.exit_date)[:10]
            contracts = t.shares / 100
            cost = t.entry_premium * t.shares
            print(
                f"  {i:>3}  {entry:10}  {exit_:10}  {contracts:>9.1f}  "
                f"${t.entry_premium:>7.2f}  ${t.exit_premium:>7.2f}  "
                f"{sign}{t.pnl_pct:>6.1%}  ${cost:>9,.0f}  {t.reason}"
            )
        print(f"  {'─'*3}  {'─'*10}  {'─'*10}  {'─'*9}  {'─'*8}  {'─'*8}  {'─'*7}  {'─'*10}  {'─'*6}")

    if show_trades and m.get("positions"):
        print(f"\n  Open positions on {end}:")
        print(f"  {'Entry':10}  {'Expiry':10}  {'Strike':>8}  {'Entry$':>8}  {'Unrealized':>10}")
        print(f"  {'─'*10}  {'─'*10}  {'─'*8}  {'─'*8}  {'─'*10}")
        for pos in m["positions"]:
            print(
                f"  {str(pos.entry_date)[:10]:10}  {str(pos.expiry_date)[:10]:10}  "
                f"${pos.strike:>7.1f}  ${pos.entry_premium:>7.2f}  (still open)"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="QQQ LEAPS backtest")
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument("--refresh", action="store_true", help="Re-download price data")
    parser.add_argument("--params", help="JSON file with parameter overrides")
    parser.add_argument("--ticker", default="QQQ", help="Underlying ticker (default: QQQ)")
    parser.add_argument("--trades", action="store_true", help="Print individual trade log")
    args = parser.parse_args()

    params = dict(DEFAULT_PARAMS)
    if args.params:
        with open(args.params) as f:
            params.update(json.load(f))

    print(f"Loading data for {args.ticker}…")
    data = load(refresh=args.refresh, ticker=args.ticker)
    print(f"Data: {data.index[0].date()} → {data.index[-1].date()}  ({len(data)} days)")

    print(f"Running backtest {args.start} → {args.end}…")
    m = run(params, args.start, args.end, data, ticker=args.ticker)
    _print_report(m, params, args.start, args.end, show_trades=args.trades)


if __name__ == "__main__":
    main()
