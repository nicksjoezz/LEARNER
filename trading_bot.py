import asyncio
import pandas as pd
import numpy as np
import time
from deriv_api import DerivAPI
from strategy_utils import ut_bot
from indicators import add_indicators
from model_manager import model_manager
import logging

class TradingBot:
    def __init__(self, socketio):
        self.socketio = socketio
        self.is_running = False
        self.start_lock = asyncio.Lock()
        self.main_task = None
        self.config = {}
        self.balance = 0.0
        self.wins = 0
        self.losses = 0
        self.total_trades = 0
        self.api = None
        self.last_candle_epoch = 0
        self.active_contracts = {} # contract_id -> {'side', 'entry_time', 'stake'}
        self.log_history = []
        self.max_logs = 100
        self.history_df = pd.DataFrame()
        self.current_symbol = ""
        self.ohlc_subscription = None

    def log(self, message):
        timestamp = time.strftime('%H:%M:%S', time.gmtime())
        full_log = f"{timestamp} | {message}"
        logging.info(full_log)
        self.log_history.append(full_log)
        if len(self.log_history) > self.max_logs:
            self.log_history.pop(0)
        self.socketio.emit('log', message)

    def update_status(self):
        self.socketio.emit('status_update', self.get_state())

    def get_state(self):
        return {
            'active': self.is_running,
            'balance': f"{self.balance:.2f}",
            'wins': self.wins,
            'losses': self.losses,
            'total_trades': self.total_trades,
            'config': self.config,
            'last_trained': model_manager.last_trained
        }

    async def cleanup_connection(self):
        if self.api:
            self.log("Closing Deriv API connection...")
            try:
                await asyncio.wait_for(self.api.disconnect(), timeout=5)
            except:
                pass
            self.api = None

    async def connect(self):
        try:
            await self.cleanup_connection()

            app_id = self.config.get('app_id')
            self.log(f"Connecting to Deriv API (App ID: {app_id})...")
            self.api = DerivAPI(app_id=app_id)

            self.log("Authorizing...")
            auth = await asyncio.wait_for(self.api.authorize(self.config['api_token']), timeout=20)

            self.balance = float(auth['authorize']['balance'])
            self.log(f"Connected to Deriv. Balance: ${self.balance:.2f}")
            self.update_status()

            # Subscribe to balance updates and contract results
            self.log("Subscribing to balance and contract updates...")
            asyncio.create_task(self.subscribe_to_updates())
            return True
        except Exception as e:
            self.log(f"Connection error: {e}")
            return False

    async def subscribe_to_updates(self):
        try:
            # Subscribe to proposal_open_contract to get results
            poc_sub = await asyncio.wait_for(self.api.subscribe({'proposal_open_contract': 1, 'subscribe': 1}), timeout=20)
            poc_sub.subscribe(self.handle_contract_update)

            # Subscribe to balance
            bal_sub = await asyncio.wait_for(self.api.subscribe({'balance': 1, 'subscribe': 1}), timeout=20)
            bal_sub.subscribe(self.handle_balance_update)
            self.log("Successfully subscribed to account updates.")
        except Exception as e:
            self.log(f"Subscription error: {e}")

    def handle_balance_update(self, data):
        if 'balance' in data:
            self.balance = float(data['balance']['balance'])
            self.update_status()

    def handle_contract_update(self, data):
        if 'proposal_open_contract' in data:
            contract = data['proposal_open_contract']
            if contract['is_sold']:
                status = contract['status'] # won, lost
                profit = float(contract['profit'])
                contract_id = contract['contract_id']

                if contract_id in self.active_contracts:
                    side = self.active_contracts[contract_id]['side']
                    if status == 'won':
                        self.wins += 1
                        self.log(f"PROFIT: {side} trade won! +${profit:.2f}")
                    else:
                        self.losses += 1
                        self.log(f"LOSS: {side} trade lost. -${abs(profit):.2f}")

                    del self.active_contracts[contract_id]
                    self.update_status()

    async def get_ml_filter(self, symbol, strategy_idx):
        ml = model_manager.get_model(symbol, strategy_idx)
        return ml

    def reset_metrics(self):
        self.wins = 0
        self.losses = 0
        self.total_trades = 0
        self.log_history = []
        self.active_contracts = {}
        self.last_candle_epoch = 0
        self.history_df = pd.DataFrame()

    async def start(self, config):
        async with self.start_lock:
            if self.is_running:
                self.log("Bot is already running. Ignoring start command.")
                return
            self.is_running = True

        self.log(f"Starting bot for {config['symbol']} Strategy {config['strategy']}...")
        self.reset_metrics()
        self.config = config
        self.current_symbol = config['symbol']

        # Ensure model is initialized or wait briefly if retraining
        strategy_idx = int(config['strategy'])
        status = model_manager.get_model_status(self.current_symbol, strategy_idx)
        if status == 'pending' or status == 'training':
            self.log(f"Model {self.current_symbol} Strat {strategy_idx} is {status}. Fallback to raw signals if not ready.")

        try:
            if await self.connect():
                self.log("Connected successfully. Initializing data buffer...")
                # Initial history fetch (500 candles for indicators)
                # Retry once if timeout
                for attempt in range(2):
                    try:
                        # Increased timeout for Railway environment
                        await asyncio.wait_for(self.fetch_initial_history(), timeout=120)
                        break
                    except asyncio.TimeoutError:
                        if attempt == 0:
                            self.log("History buffer initialization timed out. Retrying...")
                            await asyncio.sleep(5)
                        else:
                            raise

                if self.history_df.empty:
                    self.log("Failed to initialize history buffer. Stopping bot.")
                    await self.stop()
                    return

                # Start OHLC subscription
                self.log("Starting OHLC subscription loop...")
                self.main_task = asyncio.create_task(self.ohlc_subscription_loop())
                self.log("Bot initialization complete and running.")
            else:
                self.log("Failed to connect to Deriv API.")
                await self.stop()
        except asyncio.TimeoutError:
            self.log("Initialization timed out after 120s.")
            await self.stop()
        except Exception as e:
            self.log(f"Error during bot start: {e}")
            await self.stop()

    async def fetch_initial_history(self):
        symbol = self.config['symbol']
        self.log(f"Fetching initial historical data for {symbol} from API (500 candles)...")
        try:
            response = await asyncio.wait_for(self.api.ticks_history({
                'ticks_history': symbol,
                'end': 'latest',
                'count': 500,
                'granularity': 300,
                'style': 'candles'
            }), timeout=60)
            if 'candles' in response:
                self.history_df = pd.DataFrame(response['candles'])
                self.last_candle_epoch = int(self.history_df.iloc[-1]['epoch'])
                self.log(f"Initial history loaded from API: {len(self.history_df)} candles.")
            else:
                self.log(f"Error: No candles returned for {symbol} initial history.")
        except Exception as e:
            self.log(f"Error fetching initial history: {e}")

    async def stop(self):
        self.is_running = False

        if self.main_task:
            self.main_task.cancel()
            try:
                await asyncio.wait_for(self.main_task, timeout=5)
            except:
                pass
            self.main_task = None

        await self.cleanup_connection()

        self.log("Bot stopped.")
        self.update_status()

    async def ohlc_subscription_loop(self):
        symbol = self.config['symbol']
        while self.is_running:
            try:
                self.log(f"Initiating OHLC subscription for {symbol}...")
                self.ohlc_subscription = await asyncio.wait_for(self.api.subscribe({
                    'ticks_history': symbol,
                    'subscribe': 1,
                    'end': 'latest',
                    'granularity': 300,
                    'style': 'candles'
                }), timeout=30)

                self.log(f"OHLC Subscription active for {symbol}.")
                self.ohlc_subscription.subscribe(self.handle_ohlc_update)

                # Keep the task alive and monitor connection with heartbeat pings
                while self.is_running:
                    # Deriv API recommends periodic pings to keep WebSocket alive
                    await self.api.ping({'ping': 1})
                    await asyncio.sleep(30)

            except asyncio.CancelledError:
                self.log("OHLC subscription cancelled.")
                break
            except Exception as e:
                self.log(f"OHLC subscription error: {e}. Retrying in 10s...")
                await asyncio.sleep(10)
                if not self.is_running: break
                # Re-authorize if connection dropped
                try:
                    await self.connect()
                except: pass

    def handle_ohlc_update(self, data):
        if 'error' in data:
            self.log(f"Subscription error: {data['error'].get('message')}")
            return

        if 'ohlc' in data:
            ohlc = data['ohlc']
            candle_epoch = int(ohlc['open_time'])

            # Real-time update of history_df
            new_candle = {
                'epoch': int(ohlc['open_time']),
                'open': float(ohlc['open']),
                'high': float(ohlc['high']),
                'low': float(ohlc['low']),
                'close': float(ohlc['close'])
            }
            new_row = pd.DataFrame([new_candle])
            self.history_df = pd.concat([self.history_df, new_row]).drop_duplicates(subset=['epoch'], keep='last').sort_values('epoch')
            if len(self.history_df) > 500:
                self.history_df = self.history_df.iloc[-500:]

            # Detect new candle (candle closed)
            if candle_epoch > self.last_candle_epoch:
                if self.last_candle_epoch != 0:
                    self.log(f"CANDLE CLOSED: {time.ctime(self.last_candle_epoch)}")
                    # Trigger signal check immediately
                    asyncio.create_task(self.check_signals())
                else:
                    self.log(f"Bot session active. Current candle open time: {time.ctime(candle_epoch)}")
                self.last_candle_epoch = candle_epoch

    def _process_signals_sync(self, df_history, symbol, strategy_idx):
        """CPU-intensive signal calculation to be run in a thread."""
        strat_params = [
            (1, 10), (2, 20), (3, 30), (1, 20), (2, 10),
            (3, 20), (1, 30), (2, 30), (3, 10), (1.5, 15)
        ]
        a, c = strat_params[strategy_idx-1]

        df_calc = add_indicators(df_history)
        df_ut = ut_bot(df_calc, a=a, c=c)
        return df_ut

    async def check_signals(self):
        if len(self.history_df) < 200:
            self.log(f"Buffer insufficient ({len(self.history_df)} candles). Need at least 200.")
            return

        symbol = self.config['symbol']
        strategy_idx = int(self.config['strategy'])

        try:
            # Offload heavy signal processing to background thread to prevent event loop blocking
            # Copy history_df to prevent thread safety issues
            df_history = self.history_df.copy()
            df_ut = await asyncio.to_thread(self._process_signals_sync, df_history, symbol, strategy_idx)

            # Signal is checked on the candle that JUST closed (index -2)
            # Index -1 is the current building candle
            raw_sig = df_ut.iloc[-2]
            buy_triggered = raw_sig['buy']
            sell_triggered = raw_sig['sell']
            candle_time = time.ctime(int(raw_sig['epoch']))

            if buy_triggered or sell_triggered:
                side = 'BUY' if buy_triggered else 'SELL'
                self.log(f"Signal found on closed candle ({candle_time}): {side}. Verifying with Neural Filter...")

                ml = await self.get_ml_filter(symbol, strategy_idx)
                if ml:
                    # filter_signals is also CPU-intensive but currently fast enough,
                    # can be moved to thread if needed.
                    df_ml = ml.filter_signals(df_ut)
                    ml_sig = df_ml.iloc[-2]
                    if ml_sig['buy'] or ml_sig['sell']:
                        self.log(f"NEURAL FILTER: SIGNAL PASSED. Executing {side} trade.")
                        await self.place_trade('CALL' if buy_triggered else 'PUT')
                    else:
                        self.log(f"NEURAL FILTER: SIGNAL BLOCKED (Low probability).")
                else:
                    status = model_manager.get_model_status(symbol, strategy_idx)
                    self.log(f"ML filter status for {symbol} Strat {strategy_idx} is {status}. Executing raw {side} trade.")
                    await self.place_trade('CALL' if buy_triggered else 'PUT')
            else:
                self.log(f"No signal found for {symbol} on closed candle ({candle_time}).")

        except Exception as e:
            self.log(f"Error during signal check: {e}")

    async def place_trade(self, side):
        try:
            if any(c['side'] == side for c in self.active_contracts.values()):
                self.log(f"Already have an active {side} trade. Skipping.")
                return

            stake_pc = float(self.config.get('trade_pc', 1))
            amount = self.balance * (stake_pc / 100.0)
            amount = round(max(amount, 0.35), 2)

            self.log(f"PLACING {side} TRADE - Stake: ${amount:.2f}")

            proposal = await self.api.buy({
                "buy": 1,
                "price": amount,
                "parameters": {
                    "amount": amount,
                    "basis": "stake",
                    "contract_type": "CALL" if side == "CALL" else "PUT",
                    "currency": "USD",
                    "duration": 15,
                    "duration_unit": "m",
                    "symbol": self.config['symbol']
                }
            })

            if 'buy' in proposal:
                contract_id = proposal['buy']['contract_id']
                self.total_trades += 1
                self.active_contracts[contract_id] = {'side': side, 'stake': amount}
                self.log(f"SUCCESS: {side} trade placed! ID: {contract_id} | Stake: ${amount:.2f}")
            else:
                err = proposal.get('error', {}).get('message', 'Unknown error')
                self.log(f"EXECUTION ERROR: Failed to place {side} trade. Reason: {err}")

            self.update_status()

        except Exception as e:
            self.log(f"Trade placement error: {e}")

    async def main_loop(self):
        pass
