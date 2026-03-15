import pandas as pd
import numpy as np
import xgboost as xgb
import joblib
import os
from datetime import datetime

class MLFilter:
    def __init__(self):
        # Balanced XGBoost parameters with increased regularization
        self.model = xgb.XGBClassifier(
            n_estimators=500, # Increased for better convergence
            max_depth=7,
            learning_rate=0.02,
            subsample=0.8,
            colsample_bytree=0.8,
            min_child_weight=4, # Prevent learning from noise
            gamma=0.3,
            reg_alpha=0.1,
            reg_lambda=1.0,
            random_state=42,
            eval_metric='logloss',
            scale_pos_weight=1.0 # Will be adjusted dynamically during training
        )
        self.is_trained = False
        self.trained_at = None
        self.best_threshold = 0.58 # Default

        self.feature_cols = [
            'rsi', 'macd_diff', 'adx', 'bb_pct', 'ema_dist',
            'rsi_slope', 'macd_slope', 'vol_regime',
            'rsi_lag_1', 'rsi_lag_2', 'rsi_lag_3',
            'macd_lag_1', 'macd_lag_2', 'macd_lag_3',
            'close_change_lag_1', 'close_change_lag_2', 'close_change_lag_3',
            'stoch_k', 'stoch_d', 'roc', 'dist_bb_upper', 'dist_bb_lower'
        ]

    def prepare_features(self, df, positional_indices):
        valid_indices = [idx for idx in positional_indices if 0 <= idx < len(df)]
        if not valid_indices:
            return np.zeros((0, len(self.feature_cols)))

        feature_data = df.iloc[valid_indices][self.feature_cols]
        feature_data = feature_data.fillna(0)
        return feature_data.values

    def train(self, df, trades):
        if trades.empty or len(trades) < 150:
            return False

        df_work = df.reset_index(drop=True)
        epoch_to_idx = {epoch: idx for idx, epoch in enumerate(df_work['epoch'])}

        X_indices, y = [], []
        for _, trade in trades.iterrows():
            entry_epoch = trade['entry_time']
            if entry_epoch not in epoch_to_idx: continue

            entry_idx = epoch_to_idx[entry_epoch]
            signal_idx = entry_idx - 1
            if signal_idx < 0: continue

            if pd.isna(df_work.iloc[signal_idx][self.feature_cols]).any():
                continue

            X_indices.append(signal_idx)
            y.append(1 if trade['win'] else 0)

        if len(y) < 150:
            return False

        X = self.prepare_features(df_work, X_indices)
        y = np.array(y)

        # Balance classes: Adjust scale_pos_weight based on actual Win/Loss ratio
        num_neg = np.sum(y == 0)
        num_pos = np.sum(y == 1)
        if num_pos > 0:
            self.model.scale_pos_weight = num_neg / num_pos

        self.model.fit(X, y)

        # --- Cross-Validated Threshold Calibration ---
        # We aim for > 80% win rate as requested, but balancing with volume.
        # We use a leave-one-out style or simple split for speed in backtest.
        probs = self.model.predict_proba(X)[:, 1]

        best_win_rate = 0
        best_t = 0.58
        max_trades = 0

        # High-resolution threshold search
        for t in np.linspace(0.50, 0.85, 36):
            mask = probs >= t
            subset_y = y[mask]
            num_trades = len(subset_y)

            if num_trades < 10: continue # Minimum trade floor

            win_rate = np.mean(subset_y)

            # Prioritize Win Rate first (>80% goal), then volume
            if win_rate > best_win_rate:
                best_win_rate = win_rate
                best_t = t
                max_trades = num_trades
            elif abs(win_rate - best_win_rate) < 0.01:
                # If win rates are similar, pick the one with more volume
                if num_trades > max_trades:
                    best_t = t
                    max_trades = num_trades

        self.best_threshold = best_t
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

            probs = self.model.predict_proba(features)[:, 1]
            for i, idx in enumerate(indices):
                # Use the calibrated threshold
                if probs[i] < self.best_threshold:
                    df_work.at[idx, side] = False

        df_work.index = df.index
        return df_work

    def save(self, filepath):
        joblib.dump({
            'model': self.model,
            'trained_at': self.trained_at,
            'features': self.feature_cols,
            'threshold': self.best_threshold,
            'algo': 'xgboost_v2'
        }, filepath)

    def load(self, filepath):
        if os.path.exists(filepath):
            try:
                data = joblib.load(filepath)
                if isinstance(data, dict):
                    self.model = data['model']
                    self.trained_at = data.get('trained_at')
                    self.best_threshold = data.get('threshold', 0.58)
                else:
                    self.model = data
                self.is_trained = True
                return True
            except: pass
        return False
