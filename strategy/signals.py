"""Entry signals: MACD bullish divergence + VIX elevation filter."""
import pandas as pd


def compute_macd(
    prices: pd.Series,
    fast: int = 12,
    slow: int = 26,
    sig: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Return (macd_line, signal_line, histogram)."""
    ema_fast = prices.ewm(span=fast, adjust=False).mean()
    ema_slow = prices.ewm(span=slow, adjust=False).mean()
    line = ema_fast - ema_slow
    signal = line.ewm(span=sig, adjust=False).mean()
    hist = line - signal
    return line, signal, hist


def bullish_divergence(
    prices: pd.Series,
    hist: pd.Series,
    lookback: int = 20,
    min_gap: int = 5,
    neg_hist: bool = True,
) -> bool:
    """Detect MACD bullish divergence at the last bar.

    Algorithm: split the lookback window into two halves separated by min_gap.
    - Prior low  = minimum price in [i-lookback, i-min_gap]
    - Recent low = minimum price in [i-min_gap, i]
    Divergence = price_recent < price_prior  AND  hist_recent > hist_prior
    If neg_hist=True (default): also require both histogram values negative.
    If neg_hist=False: allow divergence in any MACD region (more signals).

    No look-ahead: only uses data up to and including the current bar.
    """
    n = len(prices)
    if n < lookback + 1 or min_gap >= lookback:
        return False

    p = prices.values
    h = hist.values

    prior_p = p[n - lookback : n - min_gap]
    recent_p = p[n - min_gap : n]

    if len(prior_p) == 0 or len(recent_p) == 0:
        return False

    prior_local = prior_p.argmin()
    recent_local = recent_p.argmin()

    price_prior = prior_p[prior_local]
    price_recent = recent_p[recent_local]

    hist_prior = h[n - lookback + prior_local] if n - lookback + prior_local < n else h[-lookback]
    hist_recent = h[n - min_gap + recent_local]

    divergence = price_recent < price_prior and hist_recent > hist_prior
    if neg_hist:
        return divergence and hist_prior < 0 and hist_recent < 0
    return divergence


def vix_elevated(vix: pd.Series, ma_window: int = 20) -> bool:
    """Return True if current VIX is above its own moving average."""
    if len(vix) < ma_window + 1:
        return False
    ma = float(vix.rolling(ma_window).mean().iloc[-1])
    return float(vix.iloc[-1]) > ma


def signal_strength(
    prices: pd.Series,
    hist: pd.Series,
    vix: pd.Series,
    lookback: int,
    min_gap: int,
    vix_ma_window: int,
) -> float:
    """Compute signal conviction [0.0, 1.0] when a divergence signal has fired.

    Two components (equal weight):
    - VIX excess: how far VIX is above its MA (caps at 60% above = 1.0)
    - Divergence magnitude: price drop % + MACD recovery % (combined, capped at 1.0)
    """
    # ── VIX component ────────────────────────────────────────────────────────
    vix_cur = float(vix.iloc[-1])
    vix_ma  = float(vix.rolling(vix_ma_window).mean().iloc[-1])
    vix_excess = max(0.0, (vix_cur - vix_ma) / vix_ma)  # 0 = at MA, 0.6 = 60% above
    vix_score = min(1.0, vix_excess / 0.60)

    # ── Divergence magnitude component ───────────────────────────────────────
    n = len(prices)
    p = prices.values
    h = hist.values

    prior_p  = p[n - lookback : n - min_gap]
    recent_p = p[n - min_gap  : n]
    prior_local  = prior_p.argmin()
    recent_local = recent_p.argmin()

    price_prior  = prior_p[prior_local]
    price_recent = recent_p[recent_local]
    hist_prior   = h[n - lookback + prior_local]
    hist_recent  = h[n - min_gap  + recent_local]

    price_drop_pct   = max(0.0, (price_prior - price_recent) / price_prior)
    hist_denom       = max(abs(hist_prior), 1e-6)
    hist_recovery    = max(0.0, (hist_recent - hist_prior) / hist_denom)

    # price_drop_pct typically 0.01–0.10; hist_recovery typically 0.1–2.0
    div_score = min(1.0, price_drop_pct * 8 + hist_recovery * 0.15)

    return (vix_score + div_score) / 2.0
