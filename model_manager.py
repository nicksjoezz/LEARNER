import asyncio, pandas as pd, os, time, logging, gc, sys, json
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
        self.training_lock = asyncio.Lock()
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
                json.dump({'last_trained': self.last_trained}, f)
        except: pass

    def load_metadata(self):
        if os.path.exists(self.metadata_path):
            try:
                with open(self.metadata_path, 'r') as f:
                    data = json.load(f)
                    self.last_trained = data.get('last_trained')
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
            del df
            gc.collect()
            self.log(f"Finished training all strategies for {symbol}.")
        except Exception as e:
            self.log(f"Error training {symbol}: {e}")

    async def train_symbol(self, symbol, only_pending=False):
        """Asynchronously triggers training for a single symbol using a separate thread."""
        # Use asyncio.to_thread to run the CPU-intensive training without blocking the event loop
        await asyncio.to_thread(self._train_symbol_sync, symbol, only_pending)

    async def startup_sync(self):
        """Startup synchronization: ensures data is current and decides if retraining is needed."""
        async with self.training_lock:
            if self.is_initial_training:
                return
            self.is_initial_training = True

        self.log("Starting startup data synchronization... Please wait.")
        from fetch_data import update_symbol_data

        # Check if a full retrain is needed (missing or > 24hrs)
        should_retrain = True
        if self.last_trained:
            try:
                lt = datetime.strptime(self.last_trained, '%Y-%m-%d %H:%M:%S UTC')
                if (datetime.utcnow() - lt) < timedelta(hours=24):
                    should_retrain = False
                    self.log(f"Last training was at {self.last_trained} (Less than 24h ago). Skipping full retrain.")
            except: pass

        training_tasks = []

        # Process symbols one by one for fetching to respect rate limits
        for symbol in self.symbols:
            self.log(f"Processing {symbol}: Syncing market data...")
            try:
                # Sequential fetching
                await update_symbol_data(symbol, data_dir=self.data_dir)
                self.log(f"Data sync complete for {symbol}. Triggering background training...")

                # Start training in background immediately after fetch finishes for this symbol
                # We use asyncio.create_task which will run train_symbol (which uses to_thread)
                task = asyncio.create_task(self.train_symbol(symbol, only_pending=not should_retrain))
                training_tasks.append(task)
            except Exception as e:
                self.log(f"Failed to process {symbol}: {e}")

        # Wait for all background training to complete before marking as finished
        if training_tasks:
            self.log(f"Waiting for {len(training_tasks)} symbols to finish training...")
            await asyncio.gather(*training_tasks)

        if should_retrain:
            self.last_trained = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
            self.save_metadata()

        self.is_initial_training = False
        if self.socketio:
            self.socketio.emit('training_complete', {'status': 'success'})
        self.log(f"Startup synchronization and training finished.")

    async def train_all_models(self):
        """Full retraining cycle."""
        self.log(f"Commencing full retraining cycle...")
        from fetch_data import update_symbol_data

        training_tasks = []
        for symbol in self.symbols:
            self.log(f"Updating historical data for {symbol}...")
            try:
                await update_symbol_data(symbol, data_dir=self.data_dir)
                task = asyncio.create_task(self.train_symbol(symbol, only_pending=False))
                training_tasks.append(task)
            except Exception as e:
                self.log(f"Failed to sync data for {symbol}: {e}")

        if training_tasks:
            await asyncio.gather(*training_tasks)

        self.last_trained = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
        self.save_metadata()
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
