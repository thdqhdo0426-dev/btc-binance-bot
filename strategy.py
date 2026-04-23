"""
전략 시그널 판정 로직

롱 조건 (ALL):
  1. 종가 > 직전 20봉 고가
  2. 종가 > EMA200
  3. NATR(14) <= 3.0%
  4. MACD 히스토그램 > 0

숏 조건 (ALL):
  1. 종가 < 직전 20봉 저가
  2. 종가 < EMA200
  3. NATR(14) <= 3.0%
  4. MACD 히스토그램 < 0
"""

import pandas as pd
import numpy as np
import config


def calculate_ema(series: pd.Series, period: int) -> pd.Series:
    """EMA 계산"""
    return series.ewm(span=period, adjust=False).mean()


def calculate_natr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    NATR (Normalized Average True Range) 계산
    NATR = ATR / Close * 100 (%)
    """
    high = df['high']
    low = df['low']
    close = df['close']
    prev_close = close.shift(1)
    
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    
    # Wilder's smoothing (백테스트와 동일하게)
    atr = tr.ewm(alpha=1/period, adjust=False).mean()
    natr = atr / close * 100
    return natr


def calculate_macd(series: pd.Series, fast: int = 12, slow: int = 26,
                   signal: int = 9) -> pd.DataFrame:
    """MACD 계산"""
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    
    return pd.DataFrame({
        'macd': macd_line,
        'signal': signal_line,
        'hist': histogram
    })


def check_signal(df: pd.DataFrame) -> dict:
    """
    시그널 체크 - df는 최신 완료봉까지 포함된 캔들 데이터
    df의 마지막 행이 방금 마감된 '신호봉'
    
    Returns:
        {
            'side': 'LONG' | 'SHORT' | None,
            'close_price': float,
            'ema200': float,
            'natr': float,
            'macd_hist': float,
            'high_20': float,
            'low_20': float,
            'skip_reason': str | None
        }
    """
    if len(df) < max(config.EMA_PERIOD, config.BREAKOUT_LOOKBACK + 1, config.MACD_SLOW + config.MACD_SIGNAL):
        return {'side': None, 'skip_reason': 'insufficient_data'}
    
    # 지표 계산
    ema200 = calculate_ema(df['close'], config.EMA_PERIOD)
    natr = calculate_natr(df, config.NATR_PERIOD)
    macd_df = calculate_macd(df['close'], config.MACD_FAST, config.MACD_SLOW, config.MACD_SIGNAL)
    
    # 신호봉 = 마지막 행 (방금 마감된 봉)
    last = df.iloc[-1]
    close = last['close']
    
    # 직전 20봉 (신호봉 제외한 이전 20봉)
    prev_20 = df.iloc[-(config.BREAKOUT_LOOKBACK + 1):-1]
    high_20 = prev_20['high'].max()
    low_20 = prev_20['low'].min()
    
    # 지표값 (신호봉 기준)
    ema_val = ema200.iloc[-1]
    natr_val = natr.iloc[-1]
    macd_hist = macd_df['hist'].iloc[-1]
    
    result = {
        'close_price': float(close),
        'ema200': float(ema_val),
        'natr': float(natr_val),
        'macd_hist': float(macd_hist),
        'high_20': float(high_20),
        'low_20': float(low_20),
        'side': None,
        'skip_reason': None
    }
    
    # NATR 필터 (공통)
    if natr_val > config.NATR_THRESHOLD:
        result['skip_reason'] = f'NATR {natr_val:.2f}% > {config.NATR_THRESHOLD}%'
        return result
    
    # 롱 조건
    long_cond = (
        close > high_20 and
        close > ema_val and
        macd_hist > 0
    )
    
    # 숏 조건
    short_cond = (
        close < low_20 and
        close < ema_val and
        macd_hist < 0
    )
    
    if long_cond:
        result['side'] = 'LONG'
    elif short_cond:
        result['side'] = 'SHORT'
    else:
        reasons = []
        if close <= high_20 and close >= low_20:
            reasons.append('no_breakout')
        if close > ema_val and macd_hist <= 0:
            reasons.append('macd_not_aligned_for_long')
        elif close < ema_val and macd_hist >= 0:
            reasons.append('macd_not_aligned_for_short')
        result['skip_reason'] = ','.join(reasons) if reasons else 'no_signal'
    
    return result
