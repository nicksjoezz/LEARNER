import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestClassifier
import joblib
import os
from datetime import datetime

class MLFilter:
    def __init__(self):
        # Increased trees and complexity for better pattern matching
        self.model = RandomForestClassifier(
            n_estimators=200,
            max_depth=12,
            min_samples_leaf=5,
            random_state=42
        )
        self.is_trained = False
        self.trained_at = None
        # Explicit feature columns matching indicators.py
        self.feature_cols = [
            'rsi', 'macd_diff', 'adx', 'bb_pct', 'ema_dist',
            'rsi_slope', 'macd_slope', 'vol_regime',
            'rsi_lag_1', 'rsi_lag_2', 'rsi_lag_3',
            'macd_lag_1', 'macd_lag_2', 'macd_lag_3',
            'close_change_lag_1', 'close_change_lag_2', 'close_change_lag_3'
        ]

    def prepare_features(self, df, positional_indices):
        valid_indices = [idx for idx in positional_indices if 0 <= idx < len(df)]
        if not valid_indices:
            return np.zeros((0, len(self.feature_cols)))

        feature_data = df.iloc[valid_indices][self.feature_cols]
        feature_data = feature_data.fillna(0)
        return feature_data.values

    def train(self, df, trades):
        if trades.empty or len(trades) < 150: # Slightly lower threshold for on-demand training
            return False

        df = df.reset_index(drop=True)
        epoch_to_idx = {epoch: idx for idx, epoch in enumerate(df['epoch'])}

        X_indices, y = [], []
        for _, trade in trades.iterrows():
            entry_epoch = trade['entry_time']
            if entry_epoch not in epoch_to_idx: continue

            entry_idx = epoch_to_idx[entry_epoch]
            signal_idx = entry_idx - 1 # Feature state at time of signal
            if signal_idx < 0: continue

            # Check for NaNs in feature set
            if pd.isna(df.iloc[signal_idx][self.feature_cols]).any():
                continue

            X_indices.append(signal_idx)
            y.append(1 if trade['win'] else 0)

        if len(y) < 150:
            return False

        X = self.prepare_features(df, X_indices)
        self.model.fit(X, y)
        self.is_trained = True
        self.trained_at = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
        return True

    def filter_signals(self, df):
        if not self.is_trained: return df

        df_work = df.copy().reset_index(drop=True)
        for side in ['buy', 'sell']:
            indices = df_work.index[df_work[side]].tolist()
            if not indices: continue

            features = self.prepare_features(df_work, indices)
            if len(features) == 0: continue

            # PROBABILITY THRESHOLDING: Only take trades with > 60% win confidence
            probs = self.model.predict_proba(features)
            # probs is [n_samples, 2] -> index 1 is class 1 (win)
            for i, idx in enumerate(indices):
                win_prob = probs[i][1]
                if win_prob < 0.60:
                    df_work.at[idx, side] = False

        df_work.index = df.index
        return df_work

    def save(self, filepath):
        joblib.dump({'model': self.model, 'trained_at': self.trained_at, 'features': self.feature_cols}, filepath)

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
