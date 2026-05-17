# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "yfinance>=0.2",
#   "pandas>=2.0",
#   "numpy>=1.26",
#   "pyarrow>=14.0",
#   "matplotlib>=3.8",
# ]
# ///
"""Generate OOS backtest chart: equity curve + QQQ price with trade markers."""
import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.patches as mpatches
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from backtest import run
from strategy.data import load

PARAMS_FILE = "best_params_v3.json"
START = "2025-01-01"
END = "2026-05-16"
TICKER = "QQQ"
OUT = "oos_2025_2026.png"

params = json.load(open(PARAMS_FILE))
data = load(ticker=TICKER)
m = run(params, START, END, data, ticker=TICKER)

# ── Build data frames ──────────────────────────────────────────────────────────
curve_dates = [pd.Timestamp(d) for d, _ in m["curve"]]
curve_vals  = [v for _, v in m["curve"]]

qqq_sub = data.loc[START:END]["qqq"]

trades = m["trades"]
entries = [(pd.Timestamp(str(t.entry_date)[:10]), t.entry_premium, t.pnl_pct, t.reason, t.shares) for t in trades]
exits   = [(pd.Timestamp(str(t.exit_date)[:10]),  t.exit_premium,  t.pnl_pct, t.reason) for t in trades]

# ── Figure layout ──────────────────────────────────────────────────────────────
fig, (ax1, ax2, ax3) = plt.subplots(
    3, 1, figsize=(14, 11),
    gridspec_kw={"height_ratios": [2, 1.4, 0.8]},
    sharex=True,
)
fig.patch.set_facecolor("#0f0f0f")
for ax in (ax1, ax2, ax3):
    ax.set_facecolor("#141414")
    ax.tick_params(colors="#cccccc", labelsize=9)
    ax.yaxis.label.set_color("#cccccc")
    ax.xaxis.label.set_color("#cccccc")
    for spine in ax.spines.values():
        spine.set_edgecolor("#333333")
    ax.grid(axis="y", color="#2a2a2a", linewidth=0.6)
    ax.grid(axis="x", color="#1e1e1e", linewidth=0.4)

# ── Panel 1: Portfolio NAV ─────────────────────────────────────────────────────
ax1.plot(curve_dates, curve_vals, color="#4fc3f7", linewidth=1.8, zorder=3)
ax1.fill_between(curve_dates, 100_000, curve_vals,
                 where=[v >= 100_000 for v in curve_vals],
                 alpha=0.15, color="#4fc3f7")
ax1.axhline(100_000, color="#555555", linewidth=0.8, linestyle="--")

# Mark entry/exit on NAV curve (approximate NAV at that date)
curve_df = pd.Series(curve_vals, index=curve_dates)
for entry_d, eprem, pnl, reason, shares in entries:
    nav_at = curve_df.asof(entry_d) if entry_d in curve_df.index or entry_d > curve_df.index[0] else None
    if nav_at:
        ax1.scatter(entry_d, nav_at, marker="^", s=70, color="#00e676", zorder=5)
for exit_d, xprem, pnl, reason in exits:
    nav_at = curve_df.asof(exit_d) if exit_d in curve_df.index or exit_d > curve_df.index[0] else None
    color = "#ff5252" if pnl < 0 else "#ffab40"
    if nav_at:
        ax1.scatter(exit_d, nav_at, marker="v", s=70, color=color, zorder=5)

final = curve_vals[-1]
ax1.annotate(f"${final:,.0f}", xy=(curve_dates[-1], final),
             xytext=(-60, 10), textcoords="offset points",
             color="#4fc3f7", fontsize=10, fontweight="bold")
ax1.set_ylabel("Portfolio Value (USD)", fontsize=10)
ax1.set_title(
    f"QQQ LEAPS — OOS Backtest  {START} → {END}\n"
    f"CAGR {m['cagr']:.1%}  |  MaxDD {m['max_dd']:.1%}  |  "
    f"Sharpe {m['sharpe']:.2f}  |  {m['n_trades']} trades  |  "
    f"Win Rate {m['win_rate']:.0%}",
    color="white", fontsize=11, pad=10,
)
ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x/1000:.0f}k"))

# ── Panel 2: QQQ price + trade markers ────────────────────────────────────────
ax2.plot(qqq_sub.index, qqq_sub.values, color="#b0bec5", linewidth=1.2, zorder=2)

for i, (entry_d, eprem, pnl, reason, shares) in enumerate(entries, 1):
    if entry_d in qqq_sub.index or (entry_d >= qqq_sub.index[0] and entry_d <= qqq_sub.index[-1]):
        qqq_val = qqq_sub.asof(entry_d)
        ax2.scatter(entry_d, qqq_val, marker="^", s=90, color="#00e676", zorder=5)
        ax2.annotate(f"#{i} BUY\n{shares/100:.0f}c",
                     xy=(entry_d, qqq_val), xytext=(0, 12),
                     textcoords="offset points", ha="center",
                     fontsize=7, color="#00e676")

for i, (exit_d, xprem, pnl, reason) in enumerate(exits, 1):
    if exit_d in qqq_sub.index or (exit_d >= qqq_sub.index[0] and exit_d <= qqq_sub.index[-1]):
        qqq_val = qqq_sub.asof(exit_d)
        color = "#ff5252" if pnl < 0 else "#ffab40"
        sign = "+" if pnl >= 0 else ""
        ax2.scatter(exit_d, qqq_val, marker="v", s=90, color=color, zorder=5)
        ax2.annotate(f"#{i} {reason}\n{sign}{pnl:.0%}",
                     xy=(exit_d, qqq_val), xytext=(0, -22),
                     textcoords="offset points", ha="center",
                     fontsize=7, color=color)

ax2.set_ylabel("QQQ Price (USD)", fontsize=10)

# ── Panel 3: Drawdown ─────────────────────────────────────────────────────────
nav_series = pd.Series(curve_vals, index=curve_dates)
rolling_max = nav_series.cummax()
drawdown = (nav_series - rolling_max) / rolling_max * 100

ax3.fill_between(drawdown.index, drawdown.values, 0, color="#ef5350", alpha=0.6)
ax3.axhline(0, color="#555555", linewidth=0.6)
ax3.set_ylabel("Drawdown %", fontsize=10)
ax3.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:.0f}%"))

# ── Legend ─────────────────────────────────────────────────────────────────────
legend_items = [
    mpatches.Patch(color="#00e676", label="Entry (BUY)"),
    mpatches.Patch(color="#ffab40", label="Exit — profit"),
    mpatches.Patch(color="#ff5252", label="Exit — loss / FIFO"),
]
ax2.legend(handles=legend_items, loc="upper left",
           facecolor="#1e1e1e", edgecolor="#444444",
           labelcolor="white", fontsize=8)

# ── X-axis formatting ──────────────────────────────────────────────────────────
ax3.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
ax3.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
plt.setp(ax3.xaxis.get_majorticklabels(), rotation=30, ha="right")

plt.tight_layout(rect=[0, 0, 1, 1])
plt.savefig(OUT, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
print(f"Saved → {OUT}")
