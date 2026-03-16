import pandas as pd
import numpy as np
import ta
from sklearn.ensemble import RandomForestClassifier
from strategy_utils import ut_bot, Backtester
from indicators import add_indicators
import joblib
import os

from datetime import datetime

class MLFilter:
    def __init__(self):
        self.model = RandomForestClassifier(n_estimators=100, max_depth=10, random_state=42)
        self.is_trained = False
        self.trained_at = None
        self.feature_cols = ['rsi', 'macd_diff', 'adx', 'bb_pct', 'ema_dist']

    def prepare_features(self, df, positional_indices):
        # positional_indices must be positional indices (0 to len(df)-1)
        valid_indices = [idx for idx in positional_indices if 0 <= idx < len(df)]
        if not valid_indices:
            return np.zeros((0, len(self.feature_cols)))

        # Ensure required columns exist
        if 'bb_pct' not in df.columns:
            df = df.copy()
            df['bb_pct'] = (df['close'] - df['bb_lband']) / (df['bb_hband'] - df['bb_lband'] + 1e-9)
            df['ema_dist'] = (df['close'] - df['ema_200']) / df['close']

        feature_data = df.iloc[valid_indices][['rsi', 'macd_diff', 'adx', 'bb_pct', 'ema_dist']]
        feature_data = feature_data.fillna(0)

        return feature_data.values

    def train(self, df, trades):
        if trades.empty:
            return False

        # Reset index to ensure positional indexing matches
        df = df.reset_index(drop=True)

        if 'bb_pct' not in df.columns:
            df['bb_pct'] = (df['close'] - df['bb_lband']) / (df['bb_hband'] - df['bb_lband'] + 1e-9)
            df['ema_dist'] = (df['close'] - df['ema_200']) / df['close']

        epoch_to_idx = {epoch: idx for idx, epoch in enumerate(df['epoch'])}
        X_indices, y = [], []

        for _, trade in trades.iterrows():
            entry_epoch = trade['entry_time']
            if entry_epoch not in epoch_to_idx: continue
            entry_idx = epoch_to_idx[entry_epoch]

            signal_idx = entry_idx - 1
            if signal_idx < 0: continue

            if pd.isna(df.iloc[signal_idx][['rsi', 'macd_diff', 'adx', 'bb_pct', 'ema_dist']]).any():
                continue

            X_indices.append(signal_idx)
            y.append(1 if trade['win'] else 0)

        if len(y) < 200:
            return False

        X = self.prepare_features(df, X_indices)
        self.model.fit(X, y)
        self.is_trained = True
        self.trained_at = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
        return True

    def filter_signals(self, df):
        if not self.is_trained: return df
        # We must NOT reset index of the original df because it might be used elsewhere
        # Instead, we work with a copy and reset its index for positional logic
        df_work = df.copy().reset_index(drop=True)

        if 'bb_pct' not in df_work.columns:
            df_work['bb_pct'] = (df_work['close'] - df_work['bb_lband']) / (df_work['bb_hband'] - df_work['bb_lband'] + 1e-9)
            df_work['ema_dist'] = (df_work['close'] - df_work['ema_200']) / df_work['close']

        for side in ['buy', 'sell']:
            # Get positional indices where signal is true
            mask = df_work[side].values
            indices = np.where(mask)[0]
            if len(indices) == 0: continue

            features = self.prepare_features(df_work, indices)
            if len(features) == 0: continue

            preds = self.model.predict(features)

            # Vectorized update: set side to False where prediction is 0
            # We use boolean indexing on the original mask indices
            filtered_mask = np.copy(mask)
            filtered_mask[indices] = preds.astype(bool)
            df_work[side] = filtered_mask

        # Restore the original index labels
        df_work.index = df.index
        return df_work

    def save(self, filepath):
        joblib.dump({'model': self.model, 'trained_at': self.trained_at}, filepath)

    def load(self, filepath):
        if os.path.exists(filepath):
            try:
                data = joblib.load(filepath)
                if isinstance(data, dict):
                    self.model = data['model']
                    self.trained_at = data.get('trained_at')
                else:
                    self.model = data
                self.is_trained = True
                return True
            except: pass
        return False
