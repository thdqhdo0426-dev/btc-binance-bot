"""
바이낸스 선물 주문 집행 모듈
- 레버리지 설정
- 지정가 진입 주문 (LIMIT)
- TP/SL 지정가 주문 (LIMIT, reduceOnly)
- 강제 청산 (시장가)
- 잔고/포지션 조회
- 데모 모드 지원 (demo-fapi.binance.com)
- 수량 검증 (최소 단위 체크)
"""

import logging
import math
from binance.client import Client
from binance.enums import (
    SIDE_BUY, SIDE_SELL,
    ORDER_TYPE_LIMIT, ORDER_TYPE_MARKET,
    TIME_IN_FORCE_GTC
)
import config

logger = logging.getLogger(__name__)


class BinanceFuturesExecutor:
    def __init__(self):
        self.client = Client(
            config.BINANCE_API_KEY,
            config.BINANCE_API_SECRET,
            testnet=config.USE_TESTNET
        )
        
        # 데모 모드: 엔드포인트를 데모 서버로 강제 변경
        if getattr(config, 'USE_DEMO', False):
            self.client.FUTURES_URL = 'https://demo-fapi.binance.com/fapi'
            self.client.FUTURES_DATA_URL = 'https://demo-fapi.binance.com/futures/data'
            self.client.FUTURES_API_VERSION = 'v1'
            logger.info("🧪 데모 모드 활성화 - demo-fapi.binance.com 사용")
        
        self.symbol = config.SYMBOL
        self._tick_size = None
        self._step_size = None
        self._load_symbol_info()
    
    def _load_symbol_info(self):
        """심볼별 가격/수량 정밀도 로드"""
        info = self.client.futures_exchange_info()
        for s in info['symbols']:
            if s['symbol'] == self.symbol:
                for f in s['filters']:
                    if f['filterType'] == 'PRICE_FILTER':
                        self._tick_size = float(f['tickSize'])
                    elif f['filterType'] == 'LOT_SIZE':
                        self._step_size = float(f['stepSize'])
                break
        logger.info(f"{self.symbol} tick={self._tick_size} step={self._step_size}")
    
    def _round_price(self, price: float) -> float:
        """tick size에 맞춰 가격 반올림 (내림) + 정밀도 보정"""
        if self._tick_size is None:
            return round(price, 2)
        # 부동소수점 오차 방지를 위해 round 추가 (소수점 자릿수 결정)
        decimals = max(0, -int(math.log10(self._tick_size)))
        return round(math.floor(price / self._tick_size) * self._tick_size, decimals)
    
    def _round_qty(self, qty: float) -> float:
        """step size에 맞춰 수량 반올림 (내림) + 정밀도 보정"""
        if self._step_size is None:
            return round(qty, 3)
        decimals = max(0, -int(math.log10(self._step_size)))
        return round(math.floor(qty / self._step_size) * self._step_size, decimals)
    
    def set_leverage(self, leverage: int):
        """레버리지 설정"""
        try:
            self.client.futures_change_leverage(
                symbol=self.symbol, leverage=leverage
            )
            logger.info(f"레버리지 {leverage}x 설정 완료")
        except Exception as e:
            logger.warning(f"레버리지 설정 실패 (이미 설정되어 있을 수 있음): {e}")
    
    def get_klines(self, limit: int = 300):
        """캔들 데이터 조회"""
        klines = self.client.futures_klines(
            symbol=self.symbol,
            interval=config.TIMEFRAME,
            limit=limit
        )
        import pandas as pd
        df = pd.DataFrame(klines, columns=[
            'open_time', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_volume', 'trades', 'taker_buy_base',
            'taker_buy_quote', 'ignore'
        ])
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)
        df['open_time'] = pd.to_datetime(df['open_time'], unit='ms')
        df['close_time'] = pd.to_datetime(df['close_time'], unit='ms')
        return df
    
    def get_current_price(self) -> float:
        """현재가 조회"""
        ticker = self.client.futures_symbol_ticker(symbol=self.symbol)
        return float(ticker['price'])
    
    def get_current_bar_open(self) -> float:
        """
        현재 진행중인 봉의 시가 조회
        시그널 체크 직후 호출하여 '다음 봉(=진입봉)'의 시가를 얻는다.
        """
        klines = self.client.futures_klines(
            symbol=self.symbol,
            interval=config.TIMEFRAME,
            limit=1
        )
        if not klines:
            raise RuntimeError("캔들 조회 실패")
        # [open_time, open, high, low, close, ...]
        return float(klines[0][1])
    
    def calculate_quantity(self, entry_price: float) -> float:
        """
        투입할 수량 계산 + 최소 주문량/금액 검증
        수량 = (증거금 * 레버리지) / 진입가
        """
        notional = config.POSITION_SIZE_USDT * config.LEVERAGE
        qty = notional / entry_price
        rounded = self._round_qty(qty)
        
        # 최소 수량 체크 (바이낸스 LOT_SIZE 필터)
        if self._step_size and rounded < self._step_size:
            logger.error(
                f"⚠️ 주문 수량이 최소 단위 미달! "
                f"계산={qty:.6f} BTC, 반올림={rounded}, 최소={self._step_size}"
            )
            logger.error(
                f"  → POSITION_SIZE_USDT * LEVERAGE = {notional} USDT"
            )
            min_usdt = self._step_size * entry_price
            logger.error(
                f"  → 최소 {min_usdt:.0f} USDT 이상 필요 (현재가 기준)"
            )
            raise ValueError(f"주문 수량 부족: {rounded} < {self._step_size}")
        
        # 최소 노셔널 체크 (50 USDT)
        actual_notional = rounded * entry_price
        if actual_notional < 50:
            logger.warning(
                f"⚠️ 주문 금액이 최소 50 USDT 미만! 실제 {actual_notional:.2f} USDT"
            )
        
        logger.info(f"수량 계산: {rounded} BTC (노셔널 {actual_notional:.2f} USDT)")
        return rounded
    
    def place_entry_limit(self, side: str, price: float, quantity: float) -> dict:
        """지정가 진입 주문"""
        order_side = SIDE_BUY if side == "LONG" else SIDE_SELL
        price = self._round_price(price)
        
        if config.DRY_RUN:
            logger.info(f"[DRY_RUN] 진입 주문 {side} {quantity}@{price:.2f}")
            return {'orderId': f'DRY_ENTRY_{side}', 'price': price, 'origQty': quantity}
        
        order = self.client.futures_create_order(
            symbol=self.symbol,
            side=order_side,
            type=ORDER_TYPE_LIMIT,
            timeInForce=TIME_IN_FORCE_GTC,
            quantity=quantity,
            price=str(price)
        )
        logger.info(f"진입 주문 제출: {order}")
        return order
    
    def place_tp_sl_limit(self, side: str, quantity: float,
                          tp_price: float, sl_price: float) -> tuple:
        """
        TP/SL 지정가 주문 (LIMIT + reduceOnly)
        - TP: 반대 방향 LIMIT 주문 (reduceOnly=True)
        - SL: 반대 방향 STOP (stopPrice 터치 시 LIMIT 주문 발동)
        """
        exit_side = SIDE_SELL if side == "LONG" else SIDE_BUY
        tp_price = self._round_price(tp_price)
        sl_price = self._round_price(sl_price)
        
        if config.DRY_RUN:
            logger.info(f"[DRY_RUN] TP LIMIT @ {tp_price:.2f} / SL STOP LIMIT @ {sl_price:.2f}")
            return ({'orderId': 'DRY_TP'}, {'orderId': 'DRY_SL'})
        
        # TP: LIMIT reduceOnly
        tp_order = self.client.futures_create_order(
            symbol=self.symbol,
            side=exit_side,
            type=ORDER_TYPE_LIMIT,
            timeInForce=TIME_IN_FORCE_GTC,
            quantity=quantity,
            price=str(tp_price),
            reduceOnly=True
        )
        
        # SL: STOP 타입 (stopPrice 도달 시 price로 LIMIT 발동)
        sl_order = self.client.futures_create_order(
            symbol=self.symbol,
            side=exit_side,
            type='STOP',
            timeInForce=TIME_IN_FORCE_GTC,
            quantity=quantity,
            price=str(sl_price),
            stopPrice=str(sl_price),
            reduceOnly=True,
            workingType='MARK_PRICE'
        )
        
        logger.info(
            f"TP/SL 지정가 주문 제출: "
            f"TP(LIMIT)={tp_order.get('orderId')} @{tp_price:.2f} / "
            f"SL(STOP_LIMIT)={sl_order.get('orderId')} @{sl_price:.2f}"
        )
        return tp_order, sl_order
    
    def cancel_order(self, order_id) -> bool:
        """주문 취소"""
        if config.DRY_RUN or (isinstance(order_id, str) and order_id.startswith('DRY_')):
            return True
        try:
            self.client.futures_cancel_order(symbol=self.symbol, orderId=order_id)
            return True
        except Exception as e:
            logger.warning(f"주문 취소 실패 {order_id}: {e}")
            return False
    
    def cancel_all_orders(self):
        """심볼의 모든 미체결 주문 취소"""
        if config.DRY_RUN:
            return
        try:
            self.client.futures_cancel_all_open_orders(symbol=self.symbol)
        except Exception as e:
            logger.warning(f"전체 주문 취소 실패: {e}")
    
    def close_position_market(self, side: str, quantity: float) -> dict:
        """시장가 강제 청산 (N봉 초과 시 사용)"""
        exit_side = SIDE_SELL if side == "LONG" else SIDE_BUY
        
        if config.DRY_RUN:
            logger.info(f"[DRY_RUN] 시장가 청산 {side} {quantity}")
            return {'orderId': 'DRY_CLOSE'}
        
        order = self.client.futures_create_order(
            symbol=self.symbol,
            side=exit_side,
            type=ORDER_TYPE_MARKET,
            quantity=quantity,
            reduceOnly=True
        )
        logger.info(f"시장가 청산 완료: {order}")
        return order
    
    def get_position_info(self) -> dict:
        """바이낸스에서 현재 포지션 정보 조회"""
        if config.DRY_RUN:
            return {'positionAmt': '0', 'entryPrice': '0'}
        positions = self.client.futures_position_information(symbol=self.symbol)
        for p in positions:
            if p['symbol'] == self.symbol:
                return p
        return None
    
    def get_order_status(self, order_id) -> dict:
        """주문 상태 조회"""
        if config.DRY_RUN or (isinstance(order_id, str) and order_id.startswith('DRY_')):
            return {'status': 'FILLED', 'avgPrice': '0', 'executedQty': '0'}
        return self.client.futures_get_order(symbol=self.symbol, orderId=order_id)
