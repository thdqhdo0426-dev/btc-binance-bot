# BTC 바이낸스 선물 4시간 돌파봇

## 전략 요약

### 시그널 판정 (봉 마감 시)
- **타임프레임**: 4시간봉 (바이낸스 기본, UTC 00/04/08/12/16/20 마감)
- **롱 조건** (ALL):
  1. 종가 > 직전 20봉 고가
  2. 종가 > EMA200
  3. NATR(14) ≤ 3.0%
  4. MACD 히스토그램 > 0
- **숏 조건** (ALL):
  1. 종가 < 직전 20봉 저가
  2. 종가 < EMA200
  3. NATR(14) ≤ 3.0%
  4. MACD 히스토그램 < 0

### 진입 (다음 봉 시작 직후)
- 롱: 진입봉 시가 × (1 − `ENTRY_OFFSET_PCT`) 에 **지정가 매수**
- 숏: 진입봉 시가 × (1 + `ENTRY_OFFSET_PCT`) 에 **지정가 매도**
- 오프셋은 0.N% 조정 가능 (config.py)
- **진입 주문은 N봉 청산 시점까지 유지** (GTC)
  - 진입봉에서 미체결이어도 다음 봉, 또 다음 봉에서 가격 도달 시 체결
  - 대기 시간도 N봉에 포함됨

### 청산 (TP/SL은 **진입봉 시가** 기준!)
| | 롱 | 숏 |
|---|---|---|
| TP | 시가 × (1 + 0.14) | 시가 × (1 − 0.14) |
| SL | 시가 × (1 − 0.05) | 시가 × (1 + 0.05) |

- TP/SL 모두 **LIMIT 지정가** 주문 (체결가 보장)
- TP/SL도 진입 주문과 동시에 제출됨 (reduceOnly, 포지션 체결 후 유효)
- N봉 종가 시점 도달 시:
  - 진입 체결됨 → **시장가 청산**
  - 진입 미체결 → 진입+TP+SL 전부 취소, 이번 사이클 종료

### 한 사이클 흐름
```
봉0(진입봉) 시작: 진입 LIMIT + TP + SL 3개 주문 제출
봉0~봉(N-1): 대기 (다른 시그널 모두 무시)
봉(N-1) 마감 = N봉 종가 시점:
  → 기존 사이클 종료 처리 (체결됐으면 시장가 청산, 미체결이면 취소)
  → 같은 시점에 새 시그널 체크 → 있으면 봉N에서 바로 새 진입
```

### 자금 관리
- 1포지션 단리 운영 (`POSITION_SIZE_USDT` × `LEVERAGE`)
- 동시에 1포지션만 운영 (진입 대기 중에도 새 시그널 무시)

## 파일 구조
```
btc_binance_bot/
├── config.py              # 설정 (API 키, 파라미터)
├── main.py                # 메인 루프
├── strategy.py            # 시그널 판정
├── executor.py            # 바이낸스 주문
├── database.py            # SQLite 관리
├── notifier.py            # 텔레그램 알림
├── requirements.txt
├── btcbot.service         # systemd 서비스
├── data/trades.db         # 거래 DB (자동 생성)
└── logs/bot.log           # 로그 (자동 생성)
```

## 주요 설정값 (config.py)

| 변수 | 기본값 | 설명 |
|---|---|---|
| `POSITION_SIZE_USDT` | 100 | 1포지션 증거금 (USDT) |
| `LEVERAGE` | 3 | 레버리지 (1~125) |
| `ENTRY_OFFSET_PCT` | 0.001 | 진입 지정가 오프셋 (0.1%) |
| `TAKE_PROFIT_PCT` | 0.14 | TP (+14%) |
| `STOP_LOSS_PCT` | 0.05 | SL (-5%) |
| `MAX_HOLD_BARS` | 8 | 최대 보유 봉수 |
| `NATR_THRESHOLD` | 3.0 | NATR 상한 (%) |
| `BREAKOUT_LOOKBACK` | 20 | 돌파 기준 봉수 |
| `EMA_PERIOD` | 200 | EMA 기간 |
| `USE_TESTNET` | True | 테스트넷 여부 |
| `DRY_RUN` | True | 실주문 없이 시뮬레이션 |

## 로컬 테스트 순서

### 1. 의존성 설치
```bash
cd btc_binance_bot
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. 설정
`config.py` 편집:
- `BINANCE_API_KEY`, `BINANCE_API_SECRET`: 바이낸스에서 발급 (Futures 권한만)
- `USE_TESTNET = True`: 처음엔 테스트넷으로 검증
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`: @BotFather + @userinfobot
- `POSITION_SIZE_USDT`, `LEVERAGE`: 투입 금액/레버리지
- `DRY_RUN = True`: 실제 주문 없이 로그만

### 3. 실행
```bash
python main.py
```

## 서버 배포 (추천: Vultr 도쿄 또는 AWS Lightsail 도쿄)

### 1. 서버 생성
- **Vultr 도쿄** $5/월 또는 **AWS Lightsail 도쿄** $5/월
- Ubuntu 22.04 LTS

### 2. 서버 세팅
```bash
sudo apt update && sudo apt install -y python3.11 python3.11-venv git

# 봇 파일 업로드 (scp 사용 예)
# scp -r btc_binance_bot ubuntu@서버IP:~/

cd ~/btc_binance_bot
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

nano config.py   # API 키 입력

# 시간 동기화 (바이낸스 API는 시간 오차에 민감)
sudo timedatectl set-ntp true
```

### 3. systemd 등록 (재부팅/크래시 자동 재시작)
```bash
# btcbot.service 안의 경로(/home/ubuntu/...)를 본인 환경에 맞게 수정
sudo cp btcbot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable btcbot
sudo systemctl start btcbot

sudo systemctl status btcbot
tail -f logs/bot.log
```

### 4. 중지/재시작
```bash
sudo systemctl stop btcbot
sudo systemctl restart btcbot
```

## 거래 통계 조회
```python
import database as db
print(db.get_trade_stats())
# → {'total_trades': 12, 'wins': 7, 'losses': 5, 'win_rate': 58.3, 'total_pnl_usdt': 142.5}
```

## 운영 체크리스트

### 최초 배포 전
- [ ] 바이낸스 **테스트넷**에서 `USE_TESTNET=True` + `DRY_RUN=False`로 최소 1주일 운영
- [ ] 시그널 감지 → 진입 체결 → TP/SL 동작 확인
- [ ] 텔레그램 알림 정상 수신
- [ ] N봉 타임아웃 시장가 청산 검증
- [ ] 진입 미체결 시 취소 동작 확인

### 실전 전환 시
- [ ] 소액으로 시작 (POSITION_SIZE_USDT 최소)
- [ ] 레버리지 낮게 (3x 정도)
- [ ] `USE_TESTNET = False`, `DRY_RUN = False`
- [ ] API 키 **IP 제한** 설정 (서버 고정 IP만)
- [ ] API 키 **출금 권한 OFF** 확인
- [ ] 서버 시간 동기화 (`timedatectl status`)

## 주요 로직 (main.py 흐름)

```
T=0        : 4시간봉 마감 (UTC 00/04/08/12/16/20)
T=0+10s    : 
  1) 기존 사이클 상태 확인
     - TP/SL 체결됐는지 확인
     - N봉 종가 도달했으면 → 시장가 청산 or 주문 취소
  2) 새 시그널 체크 (직전 완료봉 기준)
  3) 시그널 O + 포지션 없음 → 진입 + TP + SL 3개 주문 제출
     (TP/SL은 진입봉 시가 기준 계산)
T=0+5분~   : 대기 중 5분마다 TP/SL 체결/N봉 도달 감지
T=0+4h×N   : N봉 종가 시각 (= 다음 봉 마감 = 새 시그널 체크 시점)
             → 사이클 종료 + 즉시 새 시그널 판정
```

### 주요 상태 전이
```
IDLE (포지션 없음)
  ↓ 시그널 발생
ENTRY_PENDING (진입 미체결, TP/SL도 대기)
  ↓ 지정가 터치
POSITION_OPEN (진입 체결됨, TP/SL 유효)
  ↓ TP or SL 체결
CLOSED ────────────→ IDLE

ENTRY_PENDING / POSITION_OPEN 에서 N봉 도달 시 → 강제 종료 → IDLE
```

## 알려진 한계 / 향후 개선

- 실제 체결 수수료(0.02% × 2회) 미반영 → 수익률 계산에 오차
- 진입봉 시가 확정은 '봉 마감 10초 후' 시점의 현재가로 근사 (미세한 스큐 가능)
- WebSocket 미사용 → REST 폴링 기반 (4h 전략이라 무해하지만 고빈도에는 부적합)
- 웹 대시보드 부재 → DB 직접 조회 필요
