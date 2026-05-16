"""Position management: open/close LEAPS, tiered exit, FIFO rotation."""
from dataclasses import dataclass
from datetime import date, timedelta
from typing import NamedTuple

from strategy.options import call_price, strike_for_delta


class Trade(NamedTuple):
    entry_date: date
    exit_date: date
    entry_premium: float
    exit_premium: float
    pnl_pct: float
    reason: str
    shares: float = 0.0


@dataclass
class Position:
    entry_date: date
    expiry_date: date
    strike: float
    entry_premium: float   # BSM price per share at entry
    shares: float          # notional shares = cost / entry_premium

    @property
    def cost(self) -> float:
        return self.entry_premium * self.shares

    def months_held(self, d: date) -> float:
        return (d - self.entry_date).days / 30.44

    def tte_years(self, d: date) -> float:
        return max((self.expiry_date - d).days, 0) / 365.0

    def current_premium(self, d: date, S: float, sigma: float) -> float:
        return call_price(S, self.strike, self.tte_years(d), sigma)

    def current_value(self, d: date, S: float, sigma: float) -> float:
        return self.current_premium(d, S, sigma) * self.shares

    def pnl_pct(self, d: date, S: float, sigma: float) -> float:
        cp = self.current_premium(d, S, sigma)
        return (cp - self.entry_premium) / self.entry_premium


class Portfolio:
    def __init__(self, cash: float, max_pos: int, pos_pct: float):
        self.cash = cash
        self.max_pos = max_pos
        self.pos_pct = pos_pct
        self.positions: list[Position] = []
        self.trades: list[Trade] = []
        self.curve: list[tuple[date, float]] = []

    # ── valuation ─────────────────────────────────────────────────────────────

    def nav(self, d: date, S: float, sigma: float) -> float:
        return self.cash + sum(p.current_value(d, S, sigma) for p in self.positions)

    # ── open / close ───────────────────────────────────────────────────────────

    def _open(self, d: date, S: float, sigma: float, nav: float,
              target_delta: float, dte_days: int) -> bool:
        T = dte_days / 365.0
        K = strike_for_delta(S, T, sigma, target_delta)
        premium = call_price(S, K, T, sigma)
        if premium <= 0:
            return False
        budget = min(nav * self.pos_pct, self.cash * 0.99)
        if budget < premium:
            return False
        shares = budget / premium
        self.cash -= budget
        expiry = d + timedelta(days=dte_days)
        self.positions.append(Position(d, expiry, K, premium, shares))
        return True

    def _close(self, pos: Position, d: date, S: float, sigma: float, reason: str):
        exit_premium = pos.current_premium(d, S, sigma)
        proceeds = exit_premium * pos.shares
        pnl = (exit_premium - pos.entry_premium) / pos.entry_premium
        self.cash += proceeds
        self.positions.remove(pos)
        self.trades.append(Trade(pos.entry_date, d, pos.entry_premium, exit_premium, pnl, reason, pos.shares))

    # ── daily step ─────────────────────────────────────────────────────────────

    def step(self, d: date, S: float, sigma: float, signal: bool, params: dict) -> float:
        """Process one trading day. Returns end-of-day NAV."""
        # 1. Tiered exits
        for pos in list(self.positions):
            months = pos.months_held(d)
            pnl = pos.pnl_pct(d, S, sigma)
            reason = _exit_reason(months, pnl, params)
            if reason:
                self._close(pos, d, S, sigma, reason)

        # 2. Record NAV
        current_nav = self.nav(d, S, sigma)
        self.curve.append((d, current_nav))

        # 3. Entry logic
        if signal:
            if len(self.positions) < self.max_pos:
                self._open(d, S, sigma, current_nav, params["target_delta"], params["dte_days"])
            else:
                # FIFO: sell oldest, buy fresh
                oldest = min(self.positions, key=lambda p: p.entry_date)
                self._close(oldest, d, S, sigma, "fifo")
                fresh_nav = self.nav(d, S, sigma)
                self._open(d, S, sigma, fresh_nav, params["target_delta"], params["dte_days"])

        return self.nav(d, S, sigma)


def _exit_reason(months: float, pnl: float, p: dict) -> str | None:
    """Return exit reason string or None if position should be held."""
    if months > p["force_months"]:
        return "force"
    if months <= p["tier1_months"] and pnl >= p["tier1_profit"]:
        return "tp1"
    if months <= p["tier2_months"] and pnl >= p["tier2_profit"]:
        return "tp2"
    if months <= p["tier3_months"] and pnl >= p["tier3_profit"]:
        return "tp3"
    return None
