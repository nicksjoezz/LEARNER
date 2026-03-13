import asyncio
import pandas as pd
import numpy as np
import time
import os
import json
import logging
from handlers.live_handler import LiveHandler
from handlers.data_handler import DataHandler
from model_manager import model_manager

class TradingBot:
    def __init__(self, socketio):
        self.socketio = socketio
        self.is_running = False
        self.config = {}
        self.balance = 0.0
        self.wins = 0
        self.losses = 0
        self.total_trades = 0
        self.log_history = []
        self.max_logs = 100
        self.start_lock = asyncio.Lock()
        self.state_file = 'bot_state.json'
        self.load_state()
        self.live_handler = None
        self.ohlc_task = None
        self.data_handler = DataHandler()

    def log(self, message):
        timestamp = time.strftime('%H:%M:%S', time.gmtime())
        full_log = f"{timestamp} | {message}"
        logging.info(full_log)
        self.log_history.append(full_log)
        if len(self.log_history) > self.max_logs:
            self.log_history.pop(0)
        # Safely emit using SocketIO across threads
        self.socketio.emit('log', message)

    def update_status(self):
        # Safely emit using SocketIO across threads
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

    def save_state(self):
        # Move state saving to a background task to avoid blocking the loop
        asyncio.create_task(self._save_state_async())

    async def _save_state_async(self):
        try:
            state = {
                'wins': self.wins,
                'losses': self.losses,
                'total_trades': self.total_trades
            }
            def _write():
                with open(self.state_file, 'w') as f:
                    json.dump(state, f)
            await asyncio.to_thread(_write)
        except Exception as e:
            logging.error(f"Error saving state: {e}")

    def load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    state = json.load(f)
                    self.wins = state.get('wins', 0)
                    self.losses = state.get('losses', 0)
                    self.total_trades = state.get('total_trades', 0)
            except Exception as e:
                logging.error(f"Error loading state: {e}")

    async def start(self, config):
        async with self.start_lock:
            if self.is_running:
                return

            self.log(f"Starting bot for {config['symbol']}...")
            self.config = config
            self.is_running = True

            # Use LiveHandler for all live operations
            self.live_handler = LiveHandler(self)

            try:
                if await self.live_handler.connect(config):
                    # Subscribe account updates first to ensure we don't miss balance/contract info
                    await self.live_handler.subscribe_account()

                    if await self.live_handler.fetch_history(config['symbol']):
                        if await self.live_handler.start_trading(config['symbol']):
                            self.log("Bot initialization complete and running LIVE.")
                            self.update_status()
                            return

                self.log("Failed to initialize LiveHandler.")
                await self.stop_internal()
            except Exception as e:
                self.log(f"Error during bot start: {e}")
                await self.stop_internal()

    async def stop(self):
        async with self.start_lock:
            await self.stop_internal()

    async def stop_internal(self):
        self.is_running = False
        if self.live_handler:
            await self.live_handler.disconnect()
            self.live_handler = None
        self.log("Bot stopped.")
        self.update_status()
