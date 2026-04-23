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


def notify_entry(side: str, entry_price: float, qty: float, tp: float, sl: float):
    emoji = "🟢" if side == "LONG" else "🔴"
    send_telegram(
        f"{emoji} <b>진입 체결: {side}</b>\n"
        f"진입가: {entry_price:.2f}\n"
        f"수량: {qty:.4f} BTC\n"
        f"TP: {tp:.2f}\n"
        f"SL: {sl:.2f}"
    )


def notify_exit(side: str, exit_price: float, pnl_usdt: float, pnl_pct: float, reason: str):
    emoji = "✅" if pnl_usdt > 0 else "❌"
    send_telegram(
        f"{emoji} <b>청산: {side} ({reason})</b>\n"
        f"청산가: {exit_price:.2f}\n"
        f"손익: {pnl_usdt:+.2f} USDT ({pnl_pct:+.2f}%)"
    )


def notify_error(error_msg: str):
    send_telegram(f"⚠️ <b>에러</b>\n<code>{error_msg}</code>")
