import asyncio, pandas as pd, os, time, logging, gc, sys, json
from datetime import datetime, timedelta
from deriv_api import DerivAPI
from ml_filter import MLFilter
from strategy_utils import ut_bot, Backtester
from indicators import add_indicators

class ModelManager:
    def __init__(self, socketio=None):
        self.socketio = socketio
        self.models = {}
        self.last_trained = {} # symbol -> last_trained_date
        self.is_initial_training = False
        self.training_lock = asyncio.Lock()
        self.symbol_training_lock = asyncio.Lock() # Lock for per-symbol training to save memory
        self.symbols = ['R_100', 'R_75', 'R_50', 'R_25', 'R_10']
        self.strat_params = [(1, 10), (2, 20), (3, 30), (1, 20), (2, 10), (3, 20), (1, 30), (2, 30), (3, 10), (1.5, 15)]
        self.data_dir, self.model_dir = 'data', 'models'
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.model_dir, exist_ok=True)
        self.metadata_path = os.path.join(self.model_dir, 'last_trained.json')

    def log(self, message):
        full_msg = f"[ModelManager] {message}"
        logging.info(full_msg)
        if self.socketio:
            self.socketio.emit('log', f"[System] {message}")
            self.socketio.emit('training_progress', {'message': message})

    def save_metadata(self):
        try:
            with open(self.metadata_path, 'w') as f:
                json.dump({'last_trained_dict': self.last_trained}, f)
        except: pass

    def load_metadata(self):
        if os.path.exists(self.metadata_path):
            try:
                with open(self.metadata_path, 'r') as f:
                    data = json.load(f)
                    # Support both old string format and new dict format
                    lt = data.get('last_trained_dict')
                    if isinstance(lt, dict):
                        self.last_trained = lt
                    else:
                        self.last_trained = {}
            except: pass

    async def initialize_models(self):
        """Loads existing models from disk."""
        self.log("Initializing models from disk...")
        self.load_metadata()
        for symbol in self.symbols:
            self.models[symbol] = {}
            symbol_dir = os.path.join(self.model_dir, symbol)
            os.makedirs(symbol_dir, exist_ok=True)
            for i in range(len(self.strat_params)):
                strat_idx = i + 1
                model_path = os.path.join(symbol_dir, f"{symbol}_strat_{strat_idx}.pkl")
                ml = MLFilter()
                if ml.load(model_path):
                    self.models[symbol][strat_idx] = {'model': ml, 'status': 'ready'}
                else:
                    self.models[symbol][strat_idx] = {'status': 'pending'}
        self.log("Disk initialization complete.")

    def _train_symbol_sync(self, symbol, only_pending=False):
        """Synchronous method to train models for a single symbol, meant to be run in a thread."""
        filepath = os.path.join(self.data_dir, f"{symbol}_5m_2y.csv")
        if not os.path.exists(filepath):
            self.log(f"No data file for {symbol}, skipping training.")
            return

        try:
            self.log(f"Loading data for {symbol} training...")
            df_raw = pd.read_csv(filepath)
            from config_utils import load_config
            config = load_config()
            fetch_days = int(config.get('fetch_days', 365))
            cutoff_ts = int((datetime.now() - timedelta(days=fetch_days)).timestamp())
            df_raw = df_raw[df_raw['epoch'] >= cutoff_ts]

            df = add_indicators(df_raw)
            del df_raw
            gc.collect()

            for i, (a, c) in enumerate(self.strat_params):
                strat_idx = i + 1
                if only_pending and self.get_model_status(symbol, strat_idx) == 'ready':
                    continue

                self.log(f"Training: {symbol} Strat {strat_idx}...")
                df_ut = ut_bot(df, a=a, c=c)
                raw_trades = Backtester(df_ut).run()
                del df_ut

                if len(raw_trades) >= 200:
                    ml = MLFilter()
                    if ml.train(df, raw_trades):
                        symbol_dir = os.path.join(self.model_dir, symbol)
                        os.makedirs(symbol_dir, exist_ok=True)
                        ml.save(os.path.join(symbol_dir, f"{symbol}_strat_{strat_idx}.pkl"))
                        self.models[symbol][strat_idx] = {'model': ml, 'status': 'ready'}
                else:
                    self.log(f"Insufficient trades ({len(raw_trades)}) for {symbol} Strat {strat_idx}")

                del raw_trades
                gc.collect()
                time.sleep(0.5) # Yield time to OS

            del df
            gc.collect()
            self.log(f"Finished training all strategies for {symbol}.")
        except Exception as e:
            self.log(f"Error training {symbol}: {e}")

    async def train_symbol(self, symbol, only_pending=False):
        """Asynchronously triggers training for a single symbol using a separate thread."""
        # Ensure only one symbol is training at a time to minimize memory usage
        async with self.symbol_training_lock:
            # Use asyncio.to_thread to run the CPU-intensive training without blocking the event loop
            await asyncio.to_thread(self._train_symbol_sync, symbol, only_pending)

    async def startup_sync(self):
        """Startup synchronization: processes default symbol R_50."""
        async with self.training_lock:
            if self.is_initial_training: return
            self.is_initial_training = True

        try:
            self.log("Starting startup sync for default symbol (R_50)...")
            await self.ensure_symbol_ready('R_50')
            self.log("Startup synchronization finished.")
        finally:
            self.is_initial_training = False
            if self.socketio:
                self.socketio.emit('training_complete', {'status': 'success'})

    async def ensure_symbol_ready(self, symbol):
        """Ensures a symbol's data is updated and models are trained for the day."""
        # Use a secondary lock to allow multiple symbols to be queued but not overlap
        async with self.training_lock:
            today = datetime.utcnow().strftime('%Y-%m-%d')
            last_trained_date = self.last_trained.get(symbol, "")

            # Check if all models are 'ready' and trained today
            all_ready = all(self.get_model_status(symbol, i+1) == 'ready' for i in range(len(self.strat_params)))
            data_exists = os.path.exists(os.path.join(self.data_dir, f"{symbol}_5m_2y.csv"))

            if all_ready and data_exists and last_trained_date == today:
                self.log(f"Symbol {symbol} is already ready for today.")
                return True

            self.log(f"Preparing {symbol}: Fetching data and training ML...")
            if self.socketio:
                self.socketio.emit('training_progress', {'message': f"Syncing {symbol} market data..."})

            from handlers.data_handler import DataHandler
            data_handler = DataHandler(data_dir=self.data_dir)

            try:
                # Use a fresh connection for on-demand sync
                await data_handler.update_symbol_data(symbol)

                if self.socketio:
                    self.socketio.emit('training_progress', {'message': f"Training ML models for {symbol}..."})

                await self.train_symbol(symbol, only_pending=False)

                self.last_trained[symbol] = today
                self.save_metadata()

                if self.socketio:
                    self.socketio.emit('training_progress', {'message': f"{symbol} models ready."})

                return True
            except Exception as e:
                self.log(f"Failed to prepare {symbol}: {e}")
                if self.socketio:
                    self.socketio.emit('training_progress', {'symbol': symbol, 'status': 'failed'})
                return False

    async def train_all_models(self):
        """Daily maintenance cycle."""
        self.log(f"Commencing daily maintenance cycle...")
        # We only force update the ones that were already used/trained
        for symbol in self.last_trained.keys():
            await self.ensure_symbol_ready(symbol)
        self.log(f"Daily maintenance cycle complete.")

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
