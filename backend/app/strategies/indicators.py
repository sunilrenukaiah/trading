import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    return 100 - (100 / (1 + rs))


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    return macd_line, signal_line


def bollinger_bands(series: pd.Series, period: int = 20, std_dev: float = 2.0):
    mid = sma(series, period)
    std = series.rolling(window=period, min_periods=period).std()
    upper = mid + std_dev * std
    lower = mid - std_dev * std
    return upper, mid, lower


def candle_body(row) -> float:
    return abs(float(row["close"]) - float(row["open"]))


def candle_range(row) -> float:
    return float(row["high"]) - float(row["low"])


def upper_wick(row) -> float:
    return float(row["high"]) - max(float(row["open"]), float(row["close"]))


def lower_wick(row) -> float:
    return min(float(row["open"]), float(row["close"])) - float(row["low"])


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    ranges = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    )
    return ranges.max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr = true_range(high, low, close)
    return tr.rolling(window=period, min_periods=period).mean()


def _directional_movement(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14):
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    tr = true_range(high, low, close)
    atr_smooth = tr.rolling(window=period, min_periods=period).mean()
    plus_di = 100 * (plus_dm.rolling(window=period, min_periods=period).mean() / atr_smooth)
    minus_di = 100 * (minus_dm.rolling(window=period, min_periods=period).mean() / atr_smooth)
    dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di)).replace([float("inf"), -float("inf")], pd.NA)
    adx_line = dx.rolling(window=period, min_periods=period).mean()
    return plus_di, minus_di, adx_line


def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14):
    return _directional_movement(high, low, close, period)


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = close.diff().fillna(0).apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    return (direction * volume).cumsum()


def cmf(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, period: int = 20) -> pd.Series:
    hl_range = (high - low).replace(0, pd.NA)
    mfm = ((close - low) - (high - close)) / hl_range
    mfv = mfm * volume
    return mfv.rolling(window=period, min_periods=period).sum() / volume.rolling(
        window=period, min_periods=period
    ).sum()


def stochastic(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    k_period: int = 14,
    d_period: int = 3,
):
    lowest = low.rolling(window=k_period, min_periods=k_period).min()
    highest = high.rolling(window=k_period, min_periods=k_period).max()
    denom = (highest - lowest).replace(0, pd.NA)
    k = 100 * (close - lowest) / denom
    d = k.rolling(window=d_period, min_periods=d_period).mean()
    return k, d


def stoch_rsi(rsi_series: pd.Series, period: int = 14, k_period: int = 14, d_period: int = 3):
    lowest = rsi_series.rolling(window=period, min_periods=period).min()
    highest = rsi_series.rolling(window=period, min_periods=period).max()
    denom = (highest - lowest).replace(0, pd.NA)
    stoch = 100 * (rsi_series - lowest) / denom
    k = stoch.rolling(window=k_period, min_periods=k_period).mean()
    d = k.rolling(window=d_period, min_periods=d_period).mean()
    return k, d
