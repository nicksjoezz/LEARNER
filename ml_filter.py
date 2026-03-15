import pandas as pd
import numpy as np
import xgboost as xgb
import joblib
import os
from datetime import datetime

class MLFilter:
    def __init__(self):
        # Balanced XGBoost parameters for higher generalization and volume
        self.model = xgb.XGBClassifier(
            n_estimators=300, # Reduced to prevent hyper-specialization
            max_depth=5,     # Shallower trees generalize better and filter less
            learning_rate=0.03,
            subsample=0.75,
            colsample_bytree=0.75,
            min_child_weight=5, # Higher floor to ignore rare outlier wins
            gamma=0.5,        # More aggressive pruning
            random_state=42,
            eval_metric='logloss',
            scale_pos_weight=1.0
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

        # --- Balanced Utility Calibration ---
        # Aiming for high win rate BUT with high participation.
        probs = self.model.predict_proba(X)[:, 1]

        best_utility = -1
        best_t = 0.50
        max_trades_found = 0
        threshold_for_max_trades = 0.50

        total_signals = len(y)
        participation_floor = total_signals * 0.30 # Aim for at least 30% participation

        found_any_above_floor = False

        for t in np.linspace(0.48, 0.75, 51):
            mask = probs >= t
            subset_y = y[mask]
            num_trades = len(subset_y)

            if num_trades > max_trades_found:
                max_trades_found = num_trades
                threshold_for_max_trades = t

            if num_trades < participation_floor: continue

            found_any_above_floor = True
            win_rate = np.mean(subset_y)

            # UTILITY SCORE: (Win Rate - 0.5) * sqrt(Participation Rate)
            # This penalizes being near 50% and rewards volume.
            utility = (win_rate - 0.5) * np.sqrt(num_trades / total_signals)

            if utility > best_utility:
                best_utility = utility
                best_t = t

        if not found_any_above_floor:
            # Fallback to the threshold that gives us most trades if we can't hit the floor
            self.best_threshold = threshold_for_max_trades
        else:
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
