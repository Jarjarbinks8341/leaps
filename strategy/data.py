"""Fetch and cache QQQ + VIX historical daily data."""
import math
from pathlib import Path

import pandas as pd
import yfinance as yf

CACHE_DIR = Path(__file__).parent.parent


def _cache_path(ticker: str) -> Path:
    return CACHE_DIR / f"backtest_cache_{ticker.replace('-', '_')}.parquet"


def load(start: str = "2014-01-01", end: str | None = None, refresh: bool = False, ticker: str = "QQQ") -> pd.DataFrame:
    """Return DataFrame with columns [qqq, vix], indexed by date.

    Fetches from Yahoo Finance on first call (or when refresh=True),
    then reads from a local parquet cache on subsequent calls.
    Start at 2014 to give warm-up room for 2015 training start.
    """
    cache = _cache_path(ticker)
    if cache.exists() and not refresh:
        df = pd.read_parquet(cache)
        if not df.empty:
            return df

    px = yf.download(ticker, start=start, end=end, auto_adjust=True, progress=False)["Close"]
    vix = yf.download("^VIX", start=start, end=end, auto_adjust=True, progress=False)["Close"]

    px = px.squeeze()
    vix = vix.squeeze()

    df = pd.DataFrame({"qqq": px, "vix": vix}).dropna()
    df.index = pd.to_datetime(df.index).normalize()
    df.index.name = "date"

    df.to_parquet(cache)
    return df
