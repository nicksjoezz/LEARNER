import websocket
import json
import threading
import time
import pandas as pd
import numpy as np
import asyncio
from datetime import datetime, timedelta
from handlers.strategy_handler import StrategyHandler
from handlers.trade_handler import TradeHandler

class LiveHandler:
    def __init__(self, bot):
        self.bot = bot
        self.ws = None
        self.history_df = pd.DataFrame()
        self.last_candle_epoch = 0
        self.strategy_handler = StrategyHandler(bot)
        self.trade_handler = TradeHandler(bot)
        self.tick_count = 0
        self.is_authorized = False
        self.symbol = ""
        self.ws_thread = None
        self.loop = asyncio.get_event_loop()

    def log(self, msg):
        self.bot.log(msg)

    async def connect(self, config):
        self.config = config
        self.symbol = config['symbol']
        return True

    async def fetch_history(self, symbol):
        self.log(f"Fetching initial history for {symbol}...")
        return await asyncio.to_thread(self._fetch_history_sync, symbol)

    def _fetch_history_sync(self, symbol):
        try:
            # Use a single direct connection for history fetch
            ws = websocket.create_connection(f"wss://ws.binaryws.com/websockets/v3?app_id={self.config.get('app_id', '62845')}", timeout=30)
            ws.send(json.dumps({"authorize": self.config['api_token']}))
            auth_res = json.loads(ws.recv())

            if 'error' in auth_res:
                self.log(f"Auth error during history: {auth_res['error']['message']}")
                ws.close()
                return False

            # Request 1000 candles
            ws.send(json.dumps({
                "ticks_history": symbol,
                "end": "latest",
                "count": 1000,
                "granularity": 300,
                "style": "candles"
            }))
            hist_res = json.loads(ws.recv())
            ws.close()

            if 'candles' in hist_res:
                df = pd.DataFrame(hist_res['candles'])
                df = df.sort_values('epoch')
                for col in ['open', 'high', 'low', 'close']: df[col] = df[col].astype(float)
                self.history_df = df
                self.last_candle_epoch = int(df.iloc[-1]['epoch'])
                self.log(f"History loaded: {len(df)} candles. Last: {time.ctime(self.last_candle_epoch)}")
                return True
        except Exception as e:
            self.log(f"History fetch error: {e}")
        return False

    def start_trading(self, symbol):
        self.ws_thread = threading.Thread(target=self._run_ws, daemon=True)
        self.ws_thread.start()
        return True

    def _run_ws(self):
        url = f"wss://ws.binaryws.com/websockets/v3?app_id={self.config.get('app_id', '62845')}"
        self.ws = websocket.WebSocketApp(
            url,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close
        )
        self.ws.run_forever()

    def on_open(self, ws):
        self.log("WebSocket stream opened.")
        ws.send(json.dumps({"authorize": self.config['api_token']}))

    def on_message(self, ws, message):
        data = json.loads(message)
        msg_type = data.get('msg_type')

        if msg_type == 'authorize':
            if 'error' in data:
                self.log(f"Auth error: {data['error']['message']}")
                return
            self.log(f"Authorization successful. Balance: ${data['authorize']['balance']}")
            self.bot.balance = float(data['authorize']['balance'])
            self.is_authorized = True
            # Subscriptions
            ws.send(json.dumps({"balance": 1, "subscribe": 1}))
            ws.send(json.dumps({"proposal_open_contract": 1, "subscribe": 1}))
            ws.send(json.dumps({"ticks": self.symbol, "subscribe": 1}))

        elif msg_type == 'balance':
            self.bot.balance = float(data['balance']['balance'])
            self.loop.call_soon_threadsafe(self.bot.update_status)

        elif msg_type == 'proposal_open_contract':
            poc = data.get('proposal_open_contract')
            if poc:
                self.loop.call_soon_threadsafe(self.trade_handler.handle_contract_update, poc)

        elif msg_type == 'tick':
            self.handle_tick(data['tick'])

        elif msg_type == 'buy':
            if 'error' in data:
                self.log(f"Trade Error: {data['error']['message']}")
            else:
                self.log(f"Trade Success: {data['buy']['contract_id']}")

        elif msg_type == 'sell':
            self.log(f"Contract Closed: {data['sell']['contract_id']} Profit: {data['sell']['profit']}")

    def handle_tick(self, tick):
        price = float(tick['quote'])
        epoch = int(tick['epoch'])
        candle_start = (epoch // 300) * 300

        if self.history_df.empty: return

        last_idx = self.history_df.index[-1]
        last_candle_epoch = int(self.history_df.iloc[-1]['epoch'])

        if candle_start == last_candle_epoch:
            # Update current
            self.history_df.at[last_idx, 'close'] = price
            if price > self.history_df.at[last_idx, 'high']: self.history_df.at[last_idx, 'high'] = price
            if price < self.history_df.at[last_idx, 'low']: self.history_df.at[last_idx, 'low'] = price
        elif candle_start > last_candle_epoch:
            # New candle detected
            self.log(f"CANDLE CLOSED: {time.strftime('%H:%M:%S', time.gmtime(last_candle_epoch))}")

            new_row = pd.DataFrame([{'epoch': candle_start, 'open': price, 'high': price, 'low': price, 'close': price}])
            for col in ['open', 'high', 'low', 'close']: new_row[col] = new_row[col].astype(float)
            self.history_df = pd.concat([self.history_df, new_row], ignore_index=True)
            if len(self.history_df) > 1000: self.history_df = self.history_df.iloc[-1000:]
            self.last_candle_epoch = candle_start

            # Trigger signal check asynchronously on main loop
            asyncio.run_coroutine_threadsafe(self.check_signals(), self.loop)

        self.tick_count += 1
        if self.tick_count % 50 == 0:
            self.log(f"Live Price: {price} ({self.tick_count} ticks)")
            try: self.ws.send(json.dumps({"ping": 1}))
            except: pass

    async def check_signals(self):
        side = await self.strategy_handler.check_signals(self.history_df)
        if side:
            await self.place_trade(side)

    async def place_trade(self, side):
        opposite = 'PUT' if side == 'CALL' else 'CALL'

        # Simple wrapper for the websocket to match trade_handler expectations
        class WSWrapper:
            def __init__(self, ws): self.ws = ws
            async def buy(self, params):
                try: self.ws.send(json.dumps(params))
                except: pass
                return {}
            async def sell(self, params):
                try: self.ws.send(json.dumps(params))
                except: pass
                return {}

        await self.trade_handler.close_trades_by_side(opposite, WSWrapper(self.ws))
        await self.trade_handler.place_trade(WSWrapper(self.ws), side, self.symbol, self.bot.config['strategy'])

    def on_error(self, ws, error):
        self.log(f"Stream Error: {error}")

    def on_close(self, ws, close_status_code, close_msg):
        self.log(f"Stream Closed. Reconnecting in 5s...")
        if self.bot.is_running:
            time.sleep(5)
            # Simple reconnection logic
            self.start_trading(self.symbol)

    async def disconnect(self):
        if self.ws:
            self.ws.close()
            self.ws = None
