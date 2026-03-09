import asyncio, pandas as pd, os, time, logging, gc, sys
from datetime import datetime, timedelta
from ml_filter import MLFilter
from strategy_utils import ut_bot, Backtester
from indicators import add_indicators

class ModelManager:
    def __init__(self, socketio=None):
        self.socketio = socketio
        self.models = {}
        self.last_trained = None
        self.is_initial_training = False
        self.symbols = ['R_100', 'R_75', 'R_50', 'R_25', 'R_10']
        self.strat_params = [(1, 10), (2, 20), (3, 30), (1, 20), (2, 10), (3, 20), (1, 30), (2, 30), (3, 10), (1.5, 15)]
        self.data_dir, self.model_dir = 'data', 'models'
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.model_dir, exist_ok=True)

    def log(self, message):
        full_msg = f"[ModelManager] {message}"
        logging.info(full_msg)
        if self.socketio:
            self.socketio.emit('log', f"[System] {message}")
            self.socketio.emit('training_progress', {'message': message})

    async def initialize_models(self):
        """Loads models from disk."""
        self.log("Initializing models from disk...")
        for symbol in self.symbols:
            self.models[symbol] = {}
            for i in range(len(self.strat_params)):
                strat_idx = i + 1
                model_path = os.path.join(self.model_dir, f"{symbol}_strat_{strat_idx}.pkl")
                ml = MLFilter()
                if ml.load(model_path):
                    self.models[symbol][strat_idx] = {'model': ml, 'status': 'ready'}
                else:
                    self.models[symbol][strat_idx] = {'status': 'pending'}
        self.log("Disk initialization complete.")

    async def startup_sync(self):
        """Ensures data is current and retrains all models."""
        self.is_initial_training = True
        self.log("Starting startup data synchronization and retraining...")
        await self.train_all_models()
        self.is_initial_training = False
        if self.socketio:
            self.socketio.emit('training_complete', {'status': 'success'})

    async def train_all_models(self):
        """Full retraining for daily update or startup."""
        start_time = datetime.utcnow()
        self.log(f"Commencing retraining cycle at {start_time.strftime('%H:%M:%S UTC')}...")
        from fetch_data import update_symbol_data

        for symbol in self.symbols:
            self.log(f"Step 1/2: Updating historical data for {symbol}...")
            try:
                await update_symbol_data(symbol, data_dir=self.data_dir)
            except Exception as e:
                self.log(f"Failed to sync data for {symbol}: {e}")
                continue

            filepath = os.path.join(self.data_dir, f"{symbol}_5m_2y.csv")
            if not os.path.exists(filepath):
                self.log(f"Data file for {symbol} not found.")
                continue

            self.log(f"Step 2/2: Retraining all {symbol} strategies...")
            try:
                df_raw = pd.read_csv(filepath)
                # Ensure we only use the requested number of days from config.json
                from fetch_data import load_fetch_config
                fetch_days, _ = load_fetch_config()
                cutoff_ts = int((datetime.now() - timedelta(days=fetch_days)).timestamp())
                df_raw = df_raw[df_raw['epoch'] >= cutoff_ts]

                first_candle = datetime.fromtimestamp(df_raw['epoch'].min())
                last_candle = datetime.fromtimestamp(df_raw['epoch'].max())
                self.log(f"Training {symbol} on range: {first_candle} to {last_candle} ({len(df_raw)} candles)")

                df = add_indicators(df_raw)
                del df_raw
                gc.collect()

                for i, (a, c) in enumerate(self.strat_params):
                    strat_idx = i + 1
                    self.models[symbol][strat_idx]['status'] = 'training'
                    df_ut = ut_bot(df, a=a, c=c)
                    raw_trades = Backtester(df_ut).run()
                    del df_ut

                    if len(raw_trades) >= 200:
                        ml = MLFilter()
                        if ml.train(df, raw_trades):
                            ml.save(os.path.join(self.model_dir, f"{symbol}_strat_{strat_idx}.pkl"))
                            self.models[symbol][strat_idx] = {'model': ml, 'status': 'ready'}
                            self.log(f"Model {symbol} Strat {strat_idx} retrained successfully.")
                        else:
                            self.log(f"Model {symbol} Strat {strat_idx} training failed.")
                            if not self.get_model(symbol, strat_idx):
                                 self.models[symbol][strat_idx]['status'] = 'failed'
                    else:
                        self.log(f"Model {symbol} Strat {strat_idx} bypassed (insufficient trades).")
                        self.models[symbol][strat_idx]['status'] = 'bypassed'

                    del raw_trades
                    gc.collect()

                del df
                gc.collect()
            except Exception as e:
                self.log(f"Error during retraining for {symbol}: {e}")

        self.last_trained = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
        self.log(f"Retraining cycle complete at {self.last_trained}.")

    def get_model_status(self, symbol, strategy_idx):
        return self.models.get(symbol, {}).get(int(strategy_idx), {}).get('status', 'pending')

    def get_model(self, symbol, strategy_idx):
        m = self.models.get(symbol, {}).get(int(strategy_idx), {})
        return m['model'] if m.get('status') == 'ready' else None

    async def daily_update_loop(self):
        await self.initialize_models()
        await self.startup_sync()
        while True:
            now = datetime.utcnow()
            # Retrain daily at 00:05 UTC
            next_run = (now + timedelta(days=1)).replace(hour=0, minute=5, second=0)
            wait = (next_run - now).total_seconds()
            if wait <= 0: wait = 86400
            self.log(f"Next full update scheduled in {wait/3600:.1f} hours.")
            await asyncio.sleep(wait)
            await self.train_all_models()

model_manager = ModelManager()
