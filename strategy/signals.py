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
) -> bool:
    """Detect MACD bullish divergence at the last bar.

    Algorithm: split the lookback window into two halves separated by min_gap.
    - Prior low  = minimum price in [i-lookback, i-min_gap]
    - Recent low = minimum price in [i-min_gap, i]
    Divergence = price_recent < price_prior  AND  hist_recent > hist_prior
                 AND both histogram values are negative (bearish zone)

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

    return (
        price_recent < price_prior       # lower price low
        and hist_recent > hist_prior     # higher histogram low
        and hist_prior < 0              # both in negative (bearish) territory
        and hist_recent < 0
    )


def vix_elevated(vix: pd.Series, ma_window: int = 20) -> bool:
    """Return True if current VIX is above its own moving average."""
    if len(vix) < ma_window + 1:
        return False
    ma = float(vix.rolling(ma_window).mean().iloc[-1])
    return float(vix.iloc[-1]) > ma
