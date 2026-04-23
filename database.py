"""
SQLite 데이터베이스 관리
- positions: 현재 열려있는 포지션 (1개만 존재)
- trades: 모든 거래 내역 (진입/청산 완료된 기록)
- signals: 감지된 모든 시그널 로그

⚠️ 주의: entry_price는 실제 체결가, entry_open_price는 진입봉 시가 (TP/SL 기준가)
"""

import sqlite3
from datetime import datetime
from contextlib import contextmanager
import config


@contextmanager
def get_db():
    """DB 커넥션 컨텍스트 매니저"""
    conn = sqlite3.connect(config.DB_FILE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """DB 테이블 초기화"""
    with get_db() as conn:
        c = conn.cursor()
        
        # 현재 열린 포지션 (항상 0개 또는 1개)
        c.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                side TEXT NOT NULL,              -- 'LONG' or 'SHORT'
                entry_time TEXT NOT NULL,        -- 진입 시각 (ISO)
                entry_price REAL NOT NULL,       -- 실제 체결가
                entry_open_price REAL NOT NULL,  -- 진입봉 시가 (TP/SL 기준가)
                quantity REAL NOT NULL,          -- 수량 (BTC)
                leverage INTEGER NOT NULL,
                tp_price REAL NOT NULL,          -- TP 가격 (진입봉 시가 기준)
                sl_price REAL NOT NULL,          -- SL 가격 (진입봉 시가 기준)
                entry_bar_time TEXT NOT NULL,    -- 진입봉 시작 시각
                max_hold_until TEXT NOT NULL,    -- N봉 종가 시각
                entry_order_id TEXT,             -- 진입 주문 ID
                tp_order_id TEXT,                -- TP 주문 ID (LIMIT)
                sl_order_id TEXT,                -- SL 주문 ID (LIMIT, STOP)
                status TEXT DEFAULT 'OPEN'       -- 'OPEN' or 'CLOSED'
            )
        """)
        
        # 완료된 거래 내역
        c.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                side TEXT NOT NULL,
                entry_time TEXT NOT NULL,
                entry_price REAL NOT NULL,
                entry_open_price REAL NOT NULL,
                exit_time TEXT NOT NULL,
                exit_price REAL NOT NULL,
                quantity REAL NOT NULL,
                leverage INTEGER NOT NULL,
                pnl_usdt REAL NOT NULL,          -- 실현 손익 (USDT)
                pnl_pct REAL NOT NULL,           -- 수익률 (%) - 체결가 기준
                exit_reason TEXT NOT NULL,       -- 'TP', 'SL', 'TIMEOUT', 'MANUAL'
                fee_usdt REAL DEFAULT 0
            )
        """)
        
        # 시그널 로그 (진입 여부와 무관하게 모두 기록)
        c.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                check_time TEXT NOT NULL,
                signal_side TEXT,                -- 'LONG', 'SHORT', or NULL
                close_price REAL,
                ema200 REAL,
                natr REAL,
                macd_hist REAL,
                high_20 REAL,
                low_20 REAL,
                executed INTEGER DEFAULT 0,      -- 실제로 진입했는지
                skip_reason TEXT                 -- 진입 안 한 이유
            )
        """)


def get_open_position():
    """열려있는 포지션 조회 (0개 또는 1개)"""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM positions WHERE status = 'OPEN' LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def save_position(position: dict) -> int:
    """새 포지션 저장"""
    with get_db() as conn:
        c = conn.execute("""
            INSERT INTO positions (
                side, entry_time, entry_price, entry_open_price,
                quantity, leverage, tp_price, sl_price,
                entry_bar_time, max_hold_until,
                entry_order_id, tp_order_id, sl_order_id, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')
        """, (
            position['side'], position['entry_time'], position['entry_price'],
            position['entry_open_price'], position['quantity'], position['leverage'],
            position['tp_price'], position['sl_price'],
            position['entry_bar_time'], position['max_hold_until'],
            position.get('entry_order_id'), position.get('tp_order_id'),
            position.get('sl_order_id')
        ))
        return c.lastrowid


def close_position(position_id: int, exit_price: float, exit_reason: str,
                   pnl_usdt: float, pnl_pct: float, fee_usdt: float = 0):
    """포지션 종료 + trades에 기록"""
    with get_db() as conn:
        pos = conn.execute(
            "SELECT * FROM positions WHERE id = ?", (position_id,)
        ).fetchone()
        if not pos:
            return
        
        conn.execute(
            "UPDATE positions SET status = 'CLOSED' WHERE id = ?", (position_id,)
        )
        
        conn.execute("""
            INSERT INTO trades (
                side, entry_time, entry_price, entry_open_price,
                exit_time, exit_price, quantity, leverage,
                pnl_usdt, pnl_pct, exit_reason, fee_usdt
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            pos['side'], pos['entry_time'], pos['entry_price'], pos['entry_open_price'],
            datetime.now().isoformat(), exit_price,
            pos['quantity'], pos['leverage'], pnl_usdt, pnl_pct,
            exit_reason, fee_usdt
        ))


def log_signal(signal_data: dict):
    """시그널 체크 결과 로깅"""
    with get_db() as conn:
        conn.execute("""
            INSERT INTO signals (
                check_time, signal_side, close_price, ema200, natr,
                macd_hist, high_20, low_20, executed, skip_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            signal_data.get('check_time', datetime.now().isoformat()),
            signal_data.get('signal_side') or signal_data.get('side'),
            signal_data.get('close_price'),
            signal_data.get('ema200'),
            signal_data.get('natr'),
            signal_data.get('macd_hist'),
            signal_data.get('high_20'),
            signal_data.get('low_20'),
            1 if signal_data.get('executed') else 0,
            signal_data.get('skip_reason')
        ))


def get_trade_stats():
    """거래 통계 (승률, 누적 수익 등)"""
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) as c FROM trades").fetchone()['c']
        wins = conn.execute(
            "SELECT COUNT(*) as c FROM trades WHERE pnl_usdt > 0"
        ).fetchone()['c']
        total_pnl = conn.execute(
            "SELECT COALESCE(SUM(pnl_usdt), 0) as s FROM trades"
        ).fetchone()['s']
        
        return {
            'total_trades': total,
            'wins': wins,
            'losses': total - wins,
            'win_rate': (wins / total * 100) if total > 0 else 0,
            'total_pnl_usdt': total_pnl
        }
