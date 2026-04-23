"""
BTC 바이낸스 선물 매매봇 설정 템플릿
⚠️ 이 파일을 복사해서 config.py로 저장한 뒤, 실제 값으로 채우세요.
   config.py는 .gitignore에 포함되어 GitHub에 올라가지 않습니다.
"""

# ==================== 바이낸스 API ====================
BINANCE_API_KEY = "여기에_API_KEY_입력"
BINANCE_API_SECRET = "여기에_API_SECRET_입력"

# 테스트넷 사용 여부 (실전 API 키면 False)
USE_TESTNET = False

# 데모 모드 (바이낸스 실전 데모 트레이딩에서 발급한 키면 True)
USE_DEMO = False

# ==================== 텔레그램 알림 ====================
TELEGRAM_BOT_TOKEN = "여기에_봇_토큰_입력"
TELEGRAM_CHAT_ID = "여기에_CHAT_ID_입력"

# ==================== 거래 심볼 ====================
SYMBOL = "BTCUSDT"
TIMEFRAME = "4h"

# ==================== 자금 관리 (단리 운영) ====================
POSITION_SIZE_USDT = 400.0
LEVERAGE = 1

# ==================== 진입 조건 ====================
BREAKOUT_LOOKBACK = 20
EMA_PERIOD = 200
NATR_PERIOD = 14
NATR_THRESHOLD = 3.0
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# ==================== 주문 설정 ====================
ENTRY_OFFSET_PCT = 0.001

# ==================== 청산 조건 ====================
TAKE_PROFIT_PCT = 0.14
STOP_LOSS_PCT = 0.05
MAX_HOLD_BARS = 8

# ==================== 시스템 설정 ====================
SIGNAL_CHECK_DELAY_SECONDS = 5
LOG_FILE_PATH = "logs/bot.log"
DB_FILE_PATH = "data/trades.db"

# 디버그 모드 (True면 실제 주문 없이 시뮬레이션만)
DRY_RUN = True
