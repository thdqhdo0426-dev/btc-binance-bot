"""
텔레그램 봇 알림 모듈
"""

import requests
import logging
import config

logger = logging.getLogger(__name__)


def send_telegram(message: str, parse_mode: str = "HTML") -> bool:
    """텔레그램 메시지 전송"""
    if not config.TELEGRAM_BOT_TOKEN or "여기에" in config.TELEGRAM_BOT_TOKEN:
        logger.warning("텔레그램 토큰 미설정 - 알림 생략")
        return False
    
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": parse_mode
    }
    
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            return True
        logger.error(f"텔레그램 전송 실패: {r.status_code} {r.text}")
        return False
    except Exception as e:
        logger.exception(f"텔레그램 전송 예외: {e}")
        return False


def notify_startup():
    send_telegram(
        f"🤖 <b>BTC 선물 봇 시작</b>\n"
        f"심볼: {config.SYMBOL}\n"
        f"타임프레임: {config.TIMEFRAME}\n"
        f"포지션 사이즈: {config.POSITION_SIZE_USDT} USDT\n"
        f"레버리지: {config.LEVERAGE}x\n"
        f"테스트넷: {config.USE_TESTNET}\n"
        f"드라이런: {config.DRY_RUN}"
    )


def notify_signal(signal: dict):
    side = signal.get('side')
    if not side:
        return
    emoji = "🟢" if side == "LONG" else "🔴"
    send_telegram(
        f"{emoji} <b>시그널 감지: {side}</b>\n"
        f"종가: {signal['close_price']:.2f}\n"
        f"EMA200: {signal['ema200']:.2f}\n"
        f"NATR: {signal['natr']:.2f}%\n"
        f"MACD Hist: {signal['macd_hist']:.4f}\n"
        f"20봉 고가: {signal['high_20']:.2f}\n"
        f"20봉 저가: {signal['low_20']:.2f}"
    )


def notify_entry(side: str, entry_price: float, qty: float, tp: float, sl: float,
                 capital: float = None, weight: float = None, natr: float = None):
    """
    진입 알림
    
    Args:
        side: LONG / SHORT
        entry_price: 진입 지정가
        qty: 수량 (BTC)
        tp: TP 가격
        sl: SL 가격
        capital: 현재 자본 (USDT) - 풀복리 정보 표시용
        weight: 비중 (0.5 또는 1.0) - NATR 비중 조절 표시용
        natr: 신호봉 NATR - 비중 결정 근거 표시용
    """
    emoji = "🟢" if side == "LONG" else "🔴"
    notional = qty * entry_price
    
    msg = (
        f"{emoji} <b>진입 주문: {side}</b>\n"
        f"진입가: {entry_price:.2f}\n"
        f"수량: {qty:.4f} BTC\n"
        f"명목: {notional:.2f} USDT\n"
    )
    
    # 비중 정보 추가 (풀복리 모드)
    if weight is not None:
        weight_pct = int(weight * 100)
        weight_emoji = "💯" if weight == 1.0 else "⚖️"
        msg += f"{weight_emoji} 비중: {weight_pct}%"
        if natr is not None:
            if weight == 0.5:
                msg += f" (NATR {natr:.2f} 위험구간)\n"
            else:
                msg += f" (NATR {natr:.2f})\n"
        else:
            msg += "\n"
    
    if capital is not None:
        msg += f"💰 자본: {capital:.2f} USDT\n"
    
    msg += (
        f"\n"
        f"🎯 TP: {tp:.2f}\n"
        f"🛡 SL: {sl:.2f}"
    )
    
    send_telegram(msg)


def notify_exit(side: str, exit_price: float, pnl_usdt: float, pnl_pct: float, reason: str):
    emoji = "✅" if pnl_usdt > 0 else "❌"
    send_telegram(
        f"{emoji} <b>청산: {side} ({reason})</b>\n"
        f"청산가: {exit_price:.2f}\n"
        f"손익: {pnl_usdt:+.2f} USDT ({pnl_pct:+.2f}%)"
    )


def notify_error(error_msg: str):
    send_telegram(f"⚠️ <b>에러</b>\n<code>{error_msg}</code>")


# ==================== 텔레그램 명령어 처리 ====================

# 마지막으로 처리한 메시지 ID (중복 처리 방지)
_last_update_id = 0


def get_updates(timeout: int = 0):
    """
    텔레그램으로부터 새 메시지 받기 (long polling)
    
    Args:
        timeout: long polling 대기 시간 (초). 0이면 즉시 반환.
    
    Returns:
        새 메시지 목록 또는 빈 리스트
    """
    global _last_update_id
    
    if not config.TELEGRAM_BOT_TOKEN or "여기에" in config.TELEGRAM_BOT_TOKEN:
        return []
    
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/getUpdates"
    params = {
        "offset": _last_update_id + 1,
        "timeout": timeout,
        "allowed_updates": ["message"]
    }
    
    try:
        r = requests.get(url, params=params, timeout=timeout + 10)
        if r.status_code != 200:
            return []
        
        data = r.json()
        if not data.get("ok"):
            return []
        
        updates = data.get("result", [])
        if updates:
            _last_update_id = updates[-1]["update_id"]
        return updates
    except Exception as e:
        logger.warning(f"텔레그램 업데이트 조회 실패: {e}")
        return []


def init_update_id():
    """
    봇 시작 시 _last_update_id를 최신으로 설정 (이전 메시지들 무시)
    """
    global _last_update_id
    
    if not config.TELEGRAM_BOT_TOKEN or "여기에" in config.TELEGRAM_BOT_TOKEN:
        return
    
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/getUpdates"
    try:
        r = requests.get(url, params={"limit": 1, "offset": -1}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            updates = data.get("result", [])
            if updates:
                _last_update_id = updates[0]["update_id"]
                logger.info(f"텔레그램 업데이트 ID 초기화: {_last_update_id}")
    except Exception as e:
        logger.warning(f"업데이트 ID 초기화 실패: {e}")


def process_commands(executor=None):
    """
    텔레그램 명령어 처리
    /status, /position, /balance, /cycle, /last, /help 지원
    
    Args:
        executor: BinanceFuturesExecutor 인스턴스 (포지션/잔고 조회용)
    """
    updates = get_updates(timeout=0)
    if not updates:
        return
    
    for update in updates:
        message = update.get("message", {})
        text = message.get("text", "").strip()
        from_user = message.get("from", {})
        user_id = str(from_user.get("id", ""))
        
        # 본인만 명령 실행 가능
        if user_id != str(config.TELEGRAM_CHAT_ID):
            logger.warning(f"권한 없는 사용자 명령 무시: {user_id}")
            continue
        
        if not text.startswith("/"):
            continue
        
        cmd = text.lower().split()[0]
        logger.info(f"텔레그램 명령어 수신: {cmd}")
        
        try:
            if cmd in ("/status", "/s"):
                handle_status(executor)
            elif cmd in ("/position", "/p", "/pos"):
                handle_position(executor)
            elif cmd in ("/balance", "/b", "/bal"):
                handle_balance(executor)
            elif cmd in ("/cycle", "/c"):
                handle_cycle()
            elif cmd in ("/last", "/l"):
                handle_last()
            elif cmd in ("/help", "/h", "/start"):
                handle_help()
            else:
                send_telegram(f"❓ 알 수 없는 명령어: {cmd}\n/help 입력 시 사용 가능한 명령어 확인")
        except Exception as e:
            logger.exception(f"명령어 처리 실패: {e}")
            send_telegram(f"⚠️ 명령어 처리 중 에러:\n<code>{str(e)[:200]}</code>")


def handle_help():
    """명령어 도움말"""
    send_telegram(
        "🤖 <b>BTC 봇 명령어</b>\n\n"
        "/status - 봇 상태 + 시간 정보\n"
        "/position - 현재 포지션\n"
        "/balance - 잔고 정보\n"
        "/cycle - 진행 중 사이클\n"
        "/last - 최근 거래 결과\n"
        "/help - 이 도움말\n\n"
        "<i>줄임형: /s /p /b /c /l /h</i>"
    )


def handle_status(executor=None):
    """봇 상태 + 시간"""
    from datetime import datetime, timezone, timedelta
    import subprocess
    
    # 봇 가동 시간
    try:
        result = subprocess.run(
            ['systemctl', 'show', 'btcbot', '--property=ActiveEnterTimestamp'],
            capture_output=True, text=True, timeout=5
        )
        uptime_info = result.stdout.strip().split('=', 1)[1] if '=' in result.stdout else "알 수 없음"
    except Exception:
        uptime_info = "알 수 없음"
    
    # 현재 시각
    now_kst = datetime.now(timezone(timedelta(hours=9)))
    
    # 다음 4시간봉 마감
    hour = now_kst.hour
    next_bar_hour = ((hour // 4) + 1) * 4
    if next_bar_hour >= 24:
        next_bar_hour = next_bar_hour % 24
        next_bar = now_kst.replace(hour=next_bar_hour, minute=0, second=0, microsecond=0) + timedelta(days=1)
    else:
        next_bar = now_kst.replace(hour=next_bar_hour, minute=0, second=0, microsecond=0)
    
    remaining = next_bar - now_kst
    h = int(remaining.total_seconds() / 3600)
    m = int((remaining.total_seconds() % 3600) / 60)
    
    # 현재 포지션 정보 간단히
    pos_info = "정보 없음"
    if executor:
        try:
            binance_pos = executor.get_position_info()
            if binance_pos:
                amt = float(binance_pos.get('positionAmt', 0))
                if abs(amt) > 1e-9:
                    side = 'LONG' if amt > 0 else 'SHORT'
                    pnl = float(binance_pos.get('unRealizedProfit', 0))
                    pos_info = f"{side} {abs(amt)} BTC ({pnl:+.2f} USDT)"
                else:
                    pos_info = "없음"
        except Exception:
            pos_info = "조회 실패"
    
    send_telegram(
        f"🤖 <b>BTC 봇 상태</b>\n\n"
        f"✅ 가동 중\n"
        f"🕐 현재: {now_kst.strftime('%m-%d %H:%M')} KST\n"
        f"⏰ 다음 시그널 체크: {next_bar.strftime('%H:%M')} ({h}시간 {m}분 후)\n"
        f"📊 포지션: {pos_info}\n"
        f"🚀 시작: {uptime_info[:19] if uptime_info != '알 수 없음' else uptime_info}"
    )


def handle_position(executor=None):
    """현재 포지션 상세"""
    if not executor:
        send_telegram("⚠️ executor 없음 - 포지션 조회 불가")
        return
    
    try:
        binance_pos = executor.get_position_info()
        if not binance_pos:
            send_telegram("📊 <b>현재 포지션</b>\n\n포지션 없음")
            return
        
        amt = float(binance_pos.get('positionAmt', 0))
        if abs(amt) < 1e-9:
            send_telegram("📊 <b>현재 포지션</b>\n\n포지션 없음")
            return
        
        side = 'LONG' if amt > 0 else 'SHORT'
        entry = float(binance_pos.get('entryPrice', 0))
        mark = float(binance_pos.get('markPrice', 0))
        pnl = float(binance_pos.get('unRealizedProfit', 0))
        pct = ((mark - entry) / entry * 100) if side == 'LONG' else ((entry - mark) / entry * 100)
        leverage = binance_pos.get('leverage', 'N/A')
        isolated = binance_pos.get('isolated', False)
        
        # DB에서 TP/SL 정보
        import sqlite3
        import database as db
        pos = db.get_open_position()
        tp_info = sl_info = remaining_info = ""
        if pos:
            tp_diff = ((pos['tp_price'] - mark) / mark * 100) if side == 'LONG' else ((mark - pos['tp_price']) / mark * 100)
            sl_diff = ((mark - pos['sl_price']) / mark * 100) if side == 'LONG' else ((pos['sl_price'] - mark) / mark * 100)
            tp_info = f"\nTP: {pos['tp_price']:.2f} ({tp_diff:+.2f}% 남음)"
            sl_info = f"\nSL: {pos['sl_price']:.2f} ({sl_diff:+.2f}% 남음)"
            
            from datetime import datetime, timezone, timedelta
            try:
                max_hold = datetime.fromisoformat(pos['max_hold_until'])
                now = datetime.now(timezone.utc)
                rem = max_hold - now
                h = int(rem.total_seconds() / 3600)
                m = int((rem.total_seconds() % 3600) / 60)
                if h >= 0:
                    remaining_info = f"\n⏱ 만료까지: {h}시간 {m}분"
                else:
                    remaining_info = f"\n⏱ 만료 지남 (청산 진행 중)"
            except Exception:
                pass
        
        emoji = "🟢" if pnl >= 0 else "🔴"
        send_telegram(
            f"📊 <b>현재 포지션</b>\n\n"
            f"{emoji} <b>{side} {abs(amt)} BTC</b>\n"
            f"진입가: {entry:.2f}\n"
            f"현재가: {mark:.2f} ({pct:+.2f}%)\n"
            f"미실현: {pnl:+.2f} USDT"
            f"{tp_info}{sl_info}{remaining_info}\n"
            f"\n레버리지: {leverage}x\n"
            f"마진: {'ISOLATED' if isolated else 'CROSSED'}"
        )
    except Exception as e:
        logger.exception(f"포지션 조회 실패: {e}")
        send_telegram(f"⚠️ 포지션 조회 실패:\n<code>{str(e)[:200]}</code>")


def handle_balance(executor=None):
    """잔고 정보"""
    if not executor:
        send_telegram("⚠️ executor 없음 - 잔고 조회 불가")
        return
    
    try:
        balance = executor.client.futures_account_balance()
        usdt = next((b for b in balance if b['asset'] == 'USDT'), None)
        if not usdt:
            send_telegram("⚠️ USDT 잔고 없음")
            return
        
        total = float(usdt['balance'])
        available = float(usdt['availableBalance'])
        unrealized = float(usdt.get('crossUnPnl', 0))
        
        send_telegram(
            f"💰 <b>잔고 정보</b>\n\n"
            f"총 잔고: {total:.2f} USDT\n"
            f"사용 가능: {available:.2f} USDT\n"
            f"미실현 PnL: {unrealized:+.2f} USDT\n"
            f"\n레버리지: {config.LEVERAGE}x\n"
            f"포지션 사이즈: {config.POSITION_SIZE_USDT} USDT"
        )
    except Exception as e:
        logger.exception(f"잔고 조회 실패: {e}")
        send_telegram(f"⚠️ 잔고 조회 실패:\n<code>{str(e)[:200]}</code>")


def handle_cycle():
    """진행 중 사이클"""
    try:
        import database as db
        pos = db.get_open_position()
        if not pos:
            send_telegram("🔄 <b>진행 중 사이클</b>\n\n없음")
            return
        
        from datetime import datetime, timezone, timedelta
        try:
            max_hold = datetime.fromisoformat(pos['max_hold_until'])
            now = datetime.now(timezone.utc)
            rem = max_hold - now
            h = int(rem.total_seconds() / 3600)
            m = int((rem.total_seconds() % 3600) / 60)
            max_hold_kst = max_hold + timedelta(hours=9)
            
            entry_time = datetime.fromisoformat(pos['entry_time'])
            entry_kst = entry_time + timedelta(hours=9) if entry_time.tzinfo else entry_time
        except Exception:
            h = m = 0
            max_hold_kst = entry_kst = None
        
        send_telegram(
            f"🔄 <b>진행 중 사이클</b>\n\n"
            f"ID: {pos['id']}\n"
            f"방향: {pos['side']}\n"
            f"진입가: {pos['entry_price']:.2f}\n"
            f"TP: {pos['tp_price']:.2f}\n"
            f"SL: {pos['sl_price']:.2f}\n"
            f"모드: {pos.get('tp_order_id', 'N/A')}\n"
            f"\n진입 시각: {entry_kst.strftime('%m-%d %H:%M') if entry_kst else 'N/A'} KST\n"
            f"만료 시각: {max_hold_kst.strftime('%m-%d %H:%M') if max_hold_kst else 'N/A'} KST\n"
            f"⏱ 만료까지: {h}시간 {m}분"
        )
    except Exception as e:
        logger.exception(f"사이클 조회 실패: {e}")
        send_telegram(f"⚠️ 사이클 조회 실패:\n<code>{str(e)[:200]}</code>")


def handle_last():
    """최근 거래 결과"""
    try:
        import sqlite3
        conn = sqlite3.connect(config.DB_FILE_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT side, entry_price, exit_price, exit_reason, pnl_usdt, pnl_pct, exit_time 
            FROM positions WHERE status='CLOSED' 
            ORDER BY id DESC LIMIT 5
        """)
        rows = cursor.fetchall()
        
        # 통계
        cursor.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN pnl_usdt > 0 THEN 1 ELSE 0 END) as wins,
                SUM(pnl_usdt) as total_pnl
            FROM positions WHERE status='CLOSED'
        """)
        stats = cursor.fetchone()
        conn.close()
        
        if not rows:
            send_telegram("📜 <b>최근 거래</b>\n\n거래 기록 없음")
            return
        
        msg = "📜 <b>최근 거래 (최대 5개)</b>\n\n"
        for r in rows:
            side, ent, exit_p, reason, pnl, pct, exit_time = r
            emoji = "🟢" if pnl > 0 else "🔴"
            time_str = exit_time[5:16] if exit_time else "N/A"
            msg += f"{emoji} {side} {ent:.0f}→{exit_p:.0f} ({reason}) {pnl:+.1f} ({pct:+.1f}%)\n"
        
        msg += f"\n<b>전체 통계</b>\n"
        msg += f"거래: {stats[0]}회 | 승: {stats[1]}회"
        if stats[0] > 0:
            win_rate = stats[1] / stats[0] * 100
            msg += f" ({win_rate:.0f}%)\n"
        else:
            msg += "\n"
        msg += f"누적 손익: {stats[2] or 0:+.2f} USDT"
        
        send_telegram(msg)
    except Exception as e:
        logger.exception(f"최근 거래 조회 실패: {e}")
        send_telegram(f"⚠️ 조회 실패:\n<code>{str(e)[:200]}</code>")


# ==================== 일일 자동 보고 ====================

_last_daily_report_date = None


def maybe_send_daily_report(executor=None, hour: int = 12, minute: int = 0):
    """
    매일 지정된 시각(KST)에 한 번 자동 보고
    
    Args:
        executor: BinanceFuturesExecutor 인스턴스
        hour: 보고 시각 시 (KST, 기본 12시)
        minute: 보고 시각 분 (KST, 기본 0분)
    """
    global _last_daily_report_date
    
    from datetime import datetime, timezone, timedelta
    now_kst = datetime.now(timezone(timedelta(hours=9)))
    today = now_kst.date()
    
    # 오늘 이미 보고했으면 스킵
    if _last_daily_report_date == today:
        return
    
    # 지정된 시각 이전이면 스킵
    target_total_min = hour * 60 + minute
    now_total_min = now_kst.hour * 60 + now_kst.minute
    
    if now_total_min < target_total_min:
        return
    
    # 너무 늦게 봇이 시작된 경우 (목표 시각 + 5분 이상 지남)
    # 오늘은 보고 안 하고 내일부터 정상 작동
    if now_total_min > target_total_min + 5:
        _last_daily_report_date = today
        return
    
    # 정상 보고 시각 도달 (목표 시각 ~ +5분 이내)
    _last_daily_report_date = today
    
    try:
        import sqlite3
        conn = sqlite3.connect(config.DB_FILE_PATH)
        cursor = conn.cursor()
        
        # 어제 거래
        cursor.execute("""
            SELECT COUNT(*), SUM(pnl_usdt), 
                   SUM(CASE WHEN pnl_usdt > 0 THEN 1 ELSE 0 END)
            FROM positions WHERE status='CLOSED'
            AND date(exit_time) = date('now', '-1 day')
        """)
        yesterday = cursor.fetchone()
        
        # 전체 누적
        cursor.execute("""
            SELECT COUNT(*), SUM(pnl_usdt),
                   SUM(CASE WHEN pnl_usdt > 0 THEN 1 ELSE 0 END)
            FROM positions WHERE status='CLOSED'
        """)
        total = cursor.fetchone()
        conn.close()
        
        # 현재 포지션 + 잔고
        pos_info = "없음"
        balance_info = ""
        if executor:
            try:
                binance_pos = executor.get_position_info()
                if binance_pos:
                    amt = float(binance_pos.get('positionAmt', 0))
                    if abs(amt) > 1e-9:
                        side = 'LONG' if amt > 0 else 'SHORT'
                        pnl = float(binance_pos.get('unRealizedProfit', 0))
                        pos_info = f"{side} {abs(amt)} BTC ({pnl:+.2f} USDT)"
                
                balance = executor.client.futures_account_balance()
                usdt = next((b for b in balance if b['asset'] == 'USDT'), None)
                if usdt:
                    balance_info = f"\n💰 잔고: {float(usdt['balance']):.2f} USDT"
            except Exception:
                pass
        
        msg = f"🤖 <b>일일 보고</b>\n"
        msg += f"📅 {now_kst.strftime('%Y-%m-%d')}\n\n"
        msg += f"✅ 봇 정상 가동 중\n"
        msg += f"📊 현재 포지션: {pos_info}{balance_info}\n\n"
        
        if yesterday and yesterday[0] and yesterday[0] > 0:
            msg += f"<b>어제 거래</b>\n"
            msg += f"  거래: {yesterday[0]}회 (승 {yesterday[2]}회)\n"
            msg += f"  손익: {yesterday[1]:+.2f} USDT\n\n"
        else:
            msg += f"어제 거래 없음\n\n"
        
        if total and total[0] and total[0] > 0:
            win_rate = total[2] / total[0] * 100
            msg += f"<b>누적 통계</b>\n"
            msg += f"  거래: {total[0]}회 (승률 {win_rate:.0f}%)\n"
            msg += f"  누적 손익: {total[1]:+.2f} USDT"
        
        send_telegram(msg)
        logger.info("일일 보고 전송 완료")
    except Exception as e:
        logger.exception(f"일일 보고 실패: {e}")
