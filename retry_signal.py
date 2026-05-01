"""
Retry signal entry script - 새 흐름 호환 버전
============================================
직전봉 시그널을 재평가하고 진입 LIMIT만 제출.
이후는 봇이 자동으로 TP/SL 부착 + 청산 처리.

동작:
1. 직전 마감봉 = 시그널 봉 식별
2. 시그널 재계산 (LONG/SHORT/None)
3. 시그널 있으면 진입봉 시가 기준 진입가/TP/SL 계산
4. 진입 LIMIT만 제출 (잔고 1배만 묶음)
5. DB에 사이클 등록 (tp_order_id, sl_order_id = NULL)
6. 봇이 10초마다 체결 확인 → 체결 시 TP/SL 부착
7. 봇이 8봉 만료 시 자동 청산

사용법:
    python retry_signal.py
"""

import sys
import logging
import os
from datetime import datetime, timedelta, timezone

import config
import database as db
from strategy import check_signal
from executor import BinanceFuturesExecutor
import notifier

os.makedirs(os.path.dirname(config.LOG_FILE_PATH), exist_ok=True)
os.makedirs(os.path.dirname(config.DB_FILE_PATH), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler(config.LOG_FILE_PATH, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
UTC = timezone.utc


def get_current_bar_open_time(now_utc=None):
    if now_utc is None:
        now_utc = datetime.now(UTC)
    hour = now_utc.hour
    bar_hour = (hour // 4) * 4
    return now_utc.replace(hour=bar_hour, minute=0, second=0, microsecond=0)


def retry_signal():
    db.init_db()
    
    existing = db.get_open_position()
    if existing:
        print("[ERROR] ALREADY IN CYCLE - id={}, side={}".format(existing['id'], existing['side']))
        sys.exit(1)
    
    executor = BinanceFuturesExecutor()
    executor.set_leverage(config.LEVERAGE)
    
    now = datetime.now(UTC)
    bar_start = get_current_bar_open_time(now)
    bar_close = bar_start + timedelta(hours=4)
    
    elapsed_min = (now - bar_start).total_seconds() / 60
    remaining_min = (bar_close - now).total_seconds() / 60
    
    print("\n" + "="*60)
    print("ENTRY BAR PROGRESS")
    print("="*60)
    print("  Now (UTC):       " + now.isoformat())
    print("  Bar start:       " + bar_start.isoformat())
    print("  Bar close:       " + bar_close.isoformat())
    print("  Elapsed:         {:.0f} min".format(elapsed_min))
    print("  Remaining:       {:.0f} min".format(remaining_min))
    
    if remaining_min < 30:
        print("\n[WARN] Bar closing in {:.0f} min!".format(remaining_min))
    
    print("\nFetching klines...")
    df = executor.get_klines(limit=300)
    df_closed = df.iloc[:-1].reset_index(drop=True)
    
    signal_bar = df_closed.iloc[-1]
    print("\nSIGNAL BAR (last closed)")
    print("  Time: {}".format(signal_bar['close_time']))
    print("  OHLC: O={:.1f} H={:.1f} L={:.1f} C={:.1f}".format(
        signal_bar['open'], signal_bar['high'], signal_bar['low'], signal_bar['close']))
    
    print("\nEvaluating signal...")
    signal = check_signal(df_closed)
    
    print("\n" + "="*60)
    print("SIGNAL RESULT")
    print("="*60)
    print("  Close:        {:.2f}".format(signal.get('close_price', 0)))
    print("  EMA200:       {:.2f}".format(signal.get('ema200', 0)))
    print("  NATR:         {:.2f}%".format(signal.get('natr', 0)))
    print("  MACD Hist:    {:.4f}".format(signal.get('macd_hist', 0)))
    print("  High 20:      {:.2f}".format(signal.get('high_20', 0)))
    print("  Low 20:       {:.2f}".format(signal.get('low_20', 0)))
    print("  ----------")
    print("  >>> Signal: {}".format(signal.get('side') or 'NONE'))
    if signal.get('skip_reason'):
        print("  >>> Reason: {}".format(signal['skip_reason']))
    print("="*60)
    
    if not signal.get('side'):
        print("\nNo signal on last bar. Exit.")
        sys.exit(0)
    
    side = signal['side']
    bar_open_price = executor.get_current_bar_open()
    print("\nEntry bar open: {}".format(bar_open_price))
    
    if side == 'LONG':
        entry_limit = bar_open_price * (1 - config.ENTRY_OFFSET_PCT)
        tp_price = bar_open_price * (1 + config.TAKE_PROFIT_PCT)
        sl_price = bar_open_price * (1 - config.STOP_LOSS_PCT)
    else:
        entry_limit = bar_open_price * (1 + config.ENTRY_OFFSET_PCT)
        tp_price = bar_open_price * (1 - config.TAKE_PROFIT_PCT)
        sl_price = bar_open_price * (1 + config.STOP_LOSS_PCT)
    
    try:
        quantity = executor.calculate_quantity(bar_open_price)
    except ValueError as e:
        print("\n[ERROR] Qty calc failed: {}".format(e))
        sys.exit(1)
    
    current_price = executor.get_current_price()
    
    print("\n" + "="*60)
    print("ENTRY INFO")
    print("="*60)
    print("  Side:           {}".format(side))
    print("  Entry bar open: {:.2f}".format(bar_open_price))
    print("  Current price:  {:.2f}".format(current_price))
    print("  Diff:           {:+.2f}".format(current_price - bar_open_price))
    print("  Entry LIMIT:    {:.2f}".format(entry_limit))
    print("  TP (target):    {:.2f}".format(tp_price))
    print("  SL (target):    {:.2f}".format(sl_price))
    print("  Quantity:       {} BTC".format(quantity))
    print("  Notional:       {:.2f} USDT".format(quantity * bar_open_price))
    print("  Required margin: {:.2f} USDT".format(quantity * bar_open_price / config.LEVERAGE))
    print("  Leverage:       {}x".format(config.LEVERAGE))
    print("  DRY_RUN:        {}".format(config.DRY_RUN))
    print("="*60 + "\n")
    
    if side == 'LONG' and current_price > entry_limit:
        print("[WARN] Current > LIMIT, may not fill immediately")
        print("       (will wait until 8 bars)")
    elif side == 'SHORT' and current_price < entry_limit:
        print("[WARN] Current < LIMIT, may not fill immediately")
        print("       (will wait until 8 bars)")
    
    print("[INFO] After this script: bot will auto-attach TP/SL when filled")
    print("[INFO] If not filled within 8 bars: bot will cancel automatically\n")
    
    confirm = input("Proceed with entry? (yes/no): ").strip().lower()
    if confirm != 'yes':
        print("Cancelled.")
        sys.exit(0)
    
    # 진입 LIMIT 주문만 제출 (TP/SL은 봇이 체결 후 부착)
    print("\nPlacing entry LIMIT order...")
    try:
        entry_order = executor.place_entry_limit(side, entry_limit, quantity)
    except Exception as e:
        print("[ERROR] Entry order failed: {}".format(e))
        notifier.notify_error("Retry entry failed: {}".format(e))
        sys.exit(1)
    
    print("[OK] Entry order: {}".format(entry_order.get('orderId')))
    
    # DB에 사이클 등록 (TP/SL은 NULL로, 봇이 체결 후 가상 모드 활성화)
    max_hold_until = bar_start + timedelta(hours=4 * config.MAX_HOLD_BARS)
    
    position_id = db.save_position({
        'side': side,
        'entry_time': datetime.now(UTC).isoformat(),
        'entry_price': entry_limit,
        'entry_open_price': bar_open_price,
        'quantity': quantity,
        'leverage': config.LEVERAGE,
        'tp_price': tp_price,
        'sl_price': sl_price,
        'entry_bar_time': bar_start.isoformat(),
        'max_hold_until': max_hold_until.isoformat(),
        'entry_order_id': str(entry_order.get('orderId')),
        'tp_order_id': None,  # 봇이 체결 후 'VIRTUAL'로 표시
        'sl_order_id': None,
    })
    
    db.log_signal({**signal, 'check_time': datetime.now().isoformat(), 'executed': True})
    
    try:
        notifier.notify_signal(signal)
        notifier.notify_entry(side, entry_limit, quantity, tp_price, sl_price)
    except Exception as e:
        logger.warning("Telegram notify failed: {}".format(e))
    
    print("\n" + "="*60)
    print("RETRY ENTRY DONE!")
    print("="*60)
    print("  Cycle ID: {}".format(position_id))
    print("  Entry LIMIT: {:.2f}".format(entry_limit))
    print("  Auto close at: {} (UTC)".format(max_hold_until.isoformat()))
    print("\n  Bot will:")
    print("  - Check fill every 10 sec (until filled)")
    print("  - Attach TP/SL automatically when filled")
    print("  - Auto close at 8 bars OR TP/SL hit")
    print("="*60 + "\n")


if __name__ == "__main__":
    try:
        retry_signal()
    except KeyboardInterrupt:
        print("\nCancelled")
        sys.exit(0)
