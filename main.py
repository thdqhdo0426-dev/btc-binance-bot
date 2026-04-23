"""
메인 루프

=== 한 사이클 흐름 ===
봉0 (진입봉 = 시그널 봉의 다음 봉) 시작 시:
  - 진입 LIMIT + TP LIMIT + SL STOP_LIMIT 3개 주문 제출
  - TP/SL은 진입봉 시가 기준 계산
  - 진입 주문은 N봉 청산 시점까지 유지 (GTC)
  - 대기 중 다른 시그널 무시

봉(N-1) 마감 = N봉 종가 시점:
  - 진입 체결됨 → TP/SL 취소 + 시장가 청산
  - 진입 미체결 → 진입+TP+SL 전부 취소, DB 삭제
  → 사이클 종료

봉(N-1) 마감 = 새 시그널 체크 시점:
  → 봉(N-1)을 신호봉으로 판정해서 봉N에서 새 진입 가능
"""

import os
import time
import logging
from datetime import datetime, timedelta, timezone

import config
import database as db
from strategy import check_signal
from executor import BinanceFuturesExecutor
import notifier


# 로그/DB 폴더 자동 생성 (서버 배포 시 필수)
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


# ===== 시각 유틸 =====
UTC = timezone.utc
BAR_HOURS_UTC = [0, 4, 8, 12, 16, 20]


def get_next_bar_close_utc(now_utc: datetime = None) -> datetime:
    """다음 4시간봉 마감 시각 (UTC)"""
    if now_utc is None:
        now_utc = datetime.now(UTC)
    
    for h in BAR_HOURS_UTC:
        candidate = now_utc.replace(hour=h, minute=0, second=0, microsecond=0)
        if candidate > now_utc:
            return candidate
    tomorrow = now_utc + timedelta(days=1)
    return tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)


def get_current_bar_open_time(now_utc: datetime = None) -> datetime:
    """현재 진행중인 봉의 시작 시각"""
    if now_utc is None:
        now_utc = datetime.now(UTC)
    hour = now_utc.hour
    bar_hour = (hour // 4) * 4
    return now_utc.replace(hour=bar_hour, minute=0, second=0, microsecond=0)


def parse_iso_utc(iso_str: str) -> datetime:
    """ISO 문자열 → UTC datetime"""
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


# ===== 손익 계산 =====

def calculate_pnl_pct(side: str, entry: float, exit_price: float) -> float:
    """수익률 % (가격 변동률, 레버리지 미적용)"""
    if side == 'LONG':
        return (exit_price - entry) / entry * 100
    else:
        return (entry - exit_price) / entry * 100


def calculate_pnl_usdt(side: str, entry: float, exit_price: float,
                       quantity: float) -> float:
    """실현 손익 (USDT)"""
    if side == 'LONG':
        return (exit_price - entry) * quantity
    else:
        return (entry - exit_price) * quantity


# ===== 포지션 상태 관리 =====

def is_entry_filled(executor: BinanceFuturesExecutor, pos: dict) -> tuple:
    """
    진입 주문 체결 여부 + 실제 체결가 반환
    Returns: (filled: bool, avg_price: float)
    """
    entry_id = pos.get('entry_order_id')
    if not entry_id or entry_id.startswith('DRY_'):
        # DRY_RUN이면 체결됐다고 가정
        return (True, pos['entry_price']) if config.DRY_RUN else (False, 0.0)
    
    try:
        status = executor.get_order_status(entry_id)
        if status.get('status') == 'FILLED':
            avg = float(status.get('avgPrice', 0)) or pos['entry_price']
            return (True, avg)
        return (False, 0.0)
    except Exception as e:
        logger.warning(f"체결 상태 조회 실패: {e}")
        return (False, 0.0)


def sync_entry_price_if_filled(executor: BinanceFuturesExecutor, pos: dict):
    """진입 체결됐는데 DB 체결가가 지정가인 경우 → 실제 체결가로 업데이트"""
    filled, avg = is_entry_filled(executor, pos)
    if filled and avg > 0 and abs(avg - pos['entry_price']) > 0.01:
        with db.get_db() as conn:
            conn.execute(
                "UPDATE positions SET entry_price = ? WHERE id = ?",
                (avg, pos['id'])
            )
        logger.info(f"실제 체결가 반영: {pos['entry_price']:.2f} → {avg:.2f}")
        pos['entry_price'] = avg


def check_tp_sl_filled(executor: BinanceFuturesExecutor, pos: dict):
    """
    TP/SL 중 하나가 체결됐는지 확인 (봉 중간에 체결된 경우)
    체결됐으면 DB 동기화 + 알림
    """
    if config.DRY_RUN:
        return False
    
    binance_pos = executor.get_position_info()
    pos_amt = float(binance_pos.get('positionAmt', 0)) if binance_pos else 0
    
    if abs(pos_amt) >= 1e-9:
        return False  # 포지션 여전히 있음
    
    # 포지션이 0 → TP/SL 중 하나 체결됨
    logger.info("바이낸스 포지션 0 감지 → TP/SL 체결 확인")
    
    # 진입은 체결됐었는지 확인
    entry_filled, entry_avg = is_entry_filled(executor, pos)
    if not entry_filled:
        # 진입도 미체결이었는데 포지션이 0? → 있을 수 없는 케이스, 그냥 리턴
        return False
    
    # 실제 체결가 반영
    if entry_avg > 0:
        pos['entry_price'] = entry_avg
    
    # TP/SL 어느쪽 체결?
    exit_price = None
    exit_reason = 'UNKNOWN'
    
    try:
        if pos.get('tp_order_id'):
            tp_status = executor.get_order_status(pos['tp_order_id'])
            if tp_status.get('status') == 'FILLED':
                exit_price = float(tp_status.get('avgPrice', 0)) or pos['tp_price']
                exit_reason = 'TP'
    except Exception as e:
        logger.warning(f"TP 상태 조회 실패: {e}")
    
    if exit_price is None:
        try:
            if pos.get('sl_order_id'):
                sl_status = executor.get_order_status(pos['sl_order_id'])
                if sl_status.get('status') == 'FILLED':
                    exit_price = float(sl_status.get('avgPrice', 0)) or pos['sl_price']
                    exit_reason = 'SL'
        except Exception as e:
            logger.warning(f"SL 상태 조회 실패: {e}")
    
    if exit_price is None:
        exit_price = executor.get_current_price()
    
    pnl_pct = calculate_pnl_pct(pos['side'], pos['entry_price'], exit_price)
    pnl_usdt = calculate_pnl_usdt(pos['side'], pos['entry_price'], exit_price,
                                   pos['quantity'])
    
    db.close_position(pos['id'], exit_price, exit_reason, pnl_usdt, pnl_pct)
    notifier.notify_exit(pos['side'], exit_price, pnl_usdt, pnl_pct, exit_reason)
    executor.cancel_all_orders()  # 남은 반대편 주문 정리
    return True


def close_cycle_at_timeout(executor: BinanceFuturesExecutor, pos: dict):
    """
    N봉 종가 시점 도달 → 사이클 강제 종료
    - 진입 체결됨: 시장가 청산
    - 진입 미체결: 주문만 취소
    """
    entry_filled, entry_avg = is_entry_filled(executor, pos)
    
    # 모든 미체결 주문 취소 (진입 + TP + SL)
    executor.cancel_all_orders()
    time.sleep(1)
    
    if entry_filled:
        # 체결된 포지션은 시장가 청산
        if entry_avg > 0:
            pos['entry_price'] = entry_avg
        
        logger.info(f"N봉 종가 도달 + 진입 체결됨 → 시장가 청산 ({pos['side']})")
        executor.close_position_market(pos['side'], pos['quantity'])
        time.sleep(2)
        
        exit_price = executor.get_current_price()
        pnl_pct = calculate_pnl_pct(pos['side'], pos['entry_price'], exit_price)
        pnl_usdt = calculate_pnl_usdt(pos['side'], pos['entry_price'], exit_price,
                                       pos['quantity'])
        
        db.close_position(pos['id'], exit_price, 'TIMEOUT', pnl_usdt, pnl_pct)
        notifier.notify_exit(pos['side'], exit_price, pnl_usdt, pnl_pct, 'TIMEOUT')
    else:
        # 미체결 상태로 끝 → 주문 취소 + DB에서 포지션 삭제
        logger.info(f"N봉 종가 도달 + 진입 미체결 → 사이클 취소 ({pos['side']})")
        with db.get_db() as conn:
            conn.execute("DELETE FROM positions WHERE id = ?", (pos['id'],))
        notifier.send_telegram(
            f"⏱ <b>진입 미체결로 사이클 종료</b>\n"
            f"방향: {pos['side']}\n"
            f"지정가: {pos['entry_price']:.2f}\n"
            f"→ 주문 취소됨"
        )


def check_cycle_status(executor: BinanceFuturesExecutor):
    """
    주기적으로 호출 (5분마다):
    - TP/SL 체결 감지 → DB 동기화
    - N봉 종가 시점 도달 확인 → 사이클 종료
    - 진입 체결 감지 → 실체결가 반영
    """
    pos = db.get_open_position()
    if not pos:
        return
    
    # 1. 진입 체결됐으면 실체결가 업데이트
    sync_entry_price_if_filled(executor, pos)
    
    # 2. TP/SL 체결 확인 (체결됐으면 여기서 종료됨)
    if check_tp_sl_filled(executor, pos):
        return
    
    # 3. N봉 종가 시각 도달?
    now = datetime.now(UTC)
    max_hold_until = parse_iso_utc(pos['max_hold_until'])
    if now >= max_hold_until:
        close_cycle_at_timeout(executor, pos)


# ===== 진입 처리 =====

def try_enter_position(executor: BinanceFuturesExecutor, signal: dict):
    """
    시그널 발생 → 진입 + TP/SL 3개 주문 제출
    TP/SL은 진입봉 시가 기준 계산
    진입 주문은 N봉 종가까지 유지 (GTC)
    """
    # 이미 사이클 진행 중이면 스킵 (미체결 대기 포함)
    if db.get_open_position():
        logger.info("이미 사이클 진행 중 - 시그널 무시")
        signal['executed'] = False
        signal['skip_reason'] = 'cycle_in_progress'
        db.log_signal({**signal, 'check_time': datetime.now().isoformat()})
        return
    
    side = signal['side']
    
    # 진입봉 시가 = 방금 시작된 봉의 시가
    bar_open_price = executor.get_current_bar_open()
    logger.info(f"진입봉 시가: {bar_open_price}")
    
    # 진입 지정가 + TP + SL 계산 (모두 진입봉 시가 기준)
    if side == 'LONG':
        entry_limit = bar_open_price * (1 - config.ENTRY_OFFSET_PCT)
        tp_price = bar_open_price * (1 + config.TAKE_PROFIT_PCT)
        sl_price = bar_open_price * (1 - config.STOP_LOSS_PCT)
    else:  # SHORT
        entry_limit = bar_open_price * (1 + config.ENTRY_OFFSET_PCT)
        tp_price = bar_open_price * (1 - config.TAKE_PROFIT_PCT)
        sl_price = bar_open_price * (1 + config.STOP_LOSS_PCT)
    
    try:
        quantity = executor.calculate_quantity(bar_open_price)
    except ValueError as e:
        logger.error(f"수량 계산 실패 - 사이클 시작 불가: {e}")
        notifier.notify_error(f"수량 부족으로 진입 불가: {e}")
        return
    
    logger.info(
        f"진입 계산 [{side}] "
        f"시가={bar_open_price:.2f} 지정가={entry_limit:.2f} "
        f"TP={tp_price:.2f} SL={sl_price:.2f} 수량={quantity}"
    )
    
    # 1. 진입 지정가 주문
    try:
        entry_order = executor.place_entry_limit(side, entry_limit, quantity)
    except Exception as e:
        logger.exception(f"진입 주문 실패: {e}")
        notifier.notify_error(f"진입 주문 실패: {e}")
        return
    
    # 2. TP/SL 지정가 주문 (reduceOnly, 진입 체결 전에도 대기 가능)
    try:
        tp_order, sl_order = executor.place_tp_sl_limit(
            side, quantity, tp_price, sl_price
        )
    except Exception as e:
        logger.exception(f"TP/SL 주문 실패: {e}")
        # 진입 주문 취소하고 종료
        executor.cancel_order(entry_order.get('orderId'))
        notifier.notify_error(f"TP/SL 주문 실패 - 진입 취소: {e}")
        return
    
    # 3. 진입봉 시작 시각 + N봉 = 청산 시각
    # 진입봉 포함 N봉이므로, 진입봉 시작 + (N × 4시간) 시점이 N봉 종가
    bar_start = get_current_bar_open_time()
    max_hold_until = bar_start + timedelta(hours=4 * config.MAX_HOLD_BARS)
    
    # 4. DB 저장
    position_id = db.save_position({
        'side': side,
        'entry_time': datetime.now(UTC).isoformat(),
        'entry_price': entry_limit,  # 체결 전 → 지정가로 임시
        'entry_open_price': bar_open_price,
        'quantity': quantity,
        'leverage': config.LEVERAGE,
        'tp_price': tp_price,
        'sl_price': sl_price,
        'entry_bar_time': bar_start.isoformat(),
        'max_hold_until': max_hold_until.isoformat(),
        'entry_order_id': str(entry_order.get('orderId')),
        'tp_order_id': str(tp_order.get('orderId')),
        'sl_order_id': str(sl_order.get('orderId')),
    })
    
    signal['executed'] = True
    db.log_signal({**signal, 'check_time': datetime.now().isoformat()})
    notifier.notify_entry(side, entry_limit, quantity, tp_price, sl_price)
    
    logger.info(
        f"사이클 시작 (id={position_id}) - "
        f"N봉 청산 시각={max_hold_until.isoformat()}"
    )


# ===== 메인 루프 =====

def run_bot():
    """봇 메인 루프"""
    db.init_db()
    executor = BinanceFuturesExecutor()
    executor.set_leverage(config.LEVERAGE)
    notifier.notify_startup()
    
    logger.info("봇 시작 - 메인 루프 진입")
    
    while True:
        try:
            # 1. 다음 4시간봉 마감 시각
            next_close = get_next_bar_close_utc()
            check_time = next_close + timedelta(seconds=config.SIGNAL_CHECK_DELAY_SECONDS)
            logger.info(f"다음 체크: {check_time.isoformat()}")
            
            # 2. 대기 루프 (5분마다 사이클 상태 체크)
            while datetime.now(UTC) < check_time:
                check_cycle_status(executor)
                remaining = (check_time - datetime.now(UTC)).total_seconds()
                time.sleep(min(max(remaining, 1), 300))
            
            logger.info("=== 4시간봉 마감 ===")
            
            # 3. 봉 마감 시점 처리 순서가 중요:
            #    (a) 기존 사이클의 청산 처리 먼저 (N봉 도달 포함)
            #    (b) 그 다음 새 시그널 체크
            #    → 같은 봉 마감에서 기존 청산 후 즉시 새 진입 가능
            
            # (a) 기존 사이클 상태 확인 + 필요 시 종료
            check_cycle_status(executor)
            
            # (b) 새 시그널 체크
            df = executor.get_klines(limit=300)
            # 마지막 봉은 방금 시작된 봉 → 직전 완료봉 = iloc[-2]
            df_closed = df.iloc[:-1].reset_index(drop=True)
            
            signal = check_signal(df_closed)
            logger.info(
                f"시그널: side={signal.get('side')} "
                f"close={signal.get('close_price')} "
                f"ema={signal.get('ema200', 0):.2f} "
                f"natr={signal.get('natr', 0):.2f} "
                f"macd={signal.get('macd_hist', 0):.4f} "
                f"skip={signal.get('skip_reason')}"
            )
            
            if signal.get('side'):
                notifier.notify_signal(signal)
                try_enter_position(executor, signal)  # 내부에서 중복 체크
            else:
                db.log_signal({
                    **signal,
                    'check_time': datetime.now().isoformat(),
                    'executed': False
                })
            
        except Exception as e:
            logger.exception(f"메인 루프 에러: {e}")
            notifier.notify_error(str(e)[:200])
            time.sleep(60)


if __name__ == "__main__":
    run_bot()
