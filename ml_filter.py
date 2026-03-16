import pandas as pd
import numpy as np
import xgboost as xgb
import joblib
import os
from datetime import datetime

class MLFilter:
    def __init__(self):
        # XGBoost parameters optimized for 5m Rise/Fall (v3 configuration)
        self.model = xgb.XGBClassifier(
            n_estimators=300,
            max_depth=4,         # Shallower depth to prevent overfitting as requested
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            min_child_weight=5,
            gamma=0.2,
            random_state=42,
            eval_metric='logloss',
            scale_pos_weight=1.0  # Dynamic adjustment during training
        )
        self.is_trained = False
        self.trained_at = None
        self.best_threshold = 0.65 # Balanced target for Rise/Fall

        # New Feature Set following the requested architecture (v4)
        self.feature_cols = [
            'rsi', 'macd_diff', 'adx', 'bb_pct', 'ema_dist',
            'ema_slope', 'rsi_slope', 'rsi_zone', 'bb_width',
            'bb_mid_dist', 'ema_alignment',
            'candle_body_pct', 'candle_streak', 'atr_percentile',
            'hour', 'day_of_week', 'session',
            'rsi_lag_1', 'macd_lag_1', 'close_change_lag_1'
        ]

    def prepare_features(self, df, positional_indices):
        valid_indices = [idx for idx in positional_indices if 0 <= idx < len(df)]
        if not valid_indices:
            return np.zeros((0, len(self.feature_cols)))

        feature_data = df.iloc[valid_indices][self.feature_cols]
        feature_data = feature_data.fillna(0)
        return feature_data.values

    def train(self, df, trades):
        """
        XGBOOST PIPELINE (v3):
        1. Collect all signals with labels (1=Win, 0=Loss)
        2. Filter only signals with high-quality engineered features.
        3. Dynamically balance classes via scale_pos_weight.
        4. Train ensemble of correction trees.
        5. Calibrate threshold using Utility Score for best participation/accuracy balance.
        """
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

            # Verify features are not NaN
            if pd.isna(df_work.iloc[signal_idx][self.feature_cols]).any():
                continue

            X_indices.append(signal_idx)
            y.append(1 if trade['win'] else 0)

        if len(y) < 150:
            return False

        X = self.prepare_features(df_work, X_indices)
        y = np.array(y)

        # Class Imbalance FIX: Set scale_pos_weight to Ratio of Losses/Wins
        num_neg = np.sum(y == 0)
        num_pos = np.sum(y == 1)
        if num_pos > 0:
            self.model.scale_pos_weight = num_neg / num_pos

        # Train model
        self.model.fit(X, y)

        # --- Accuracy Safety Gate ---
        # Evaluate training accuracy as a baseline sanity check
        train_preds = self.model.predict(X)
        train_acc = np.mean(train_preds == y)
        if train_acc < 0.52:
            # If the model can't even fit the training data better than a coin flip, reject it.
            return False

        # --- Threshold Tuning Phase ---
        probs = self.model.predict_proba(X)[:, 1]
        best_utility = -1
        best_t = 0.62 # Target Floor

        total_signals = len(y)
        # We need enough volume to be profitable, but enough winrate to survive.
        participation_floor = total_signals * 0.40 # Target at least 40% retention

        found_above_floor = False
        for t in np.linspace(0.55, 0.75, 41):
            mask = probs >= t
            subset_y = y[mask]
            num_trades = len(subset_y)

            if num_trades < participation_floor: continue

            found_above_floor = True
            win_rate = np.mean(subset_y)
            # Utility favors higher accuracy above 50%
            utility = (win_rate - 0.5) * np.log1p(num_trades)

            if utility > best_utility:
                best_utility = utility
                best_t = t

        if not found_above_floor:
            # If no threshold hits the volume floor, use the one that gives most volume at >55% accuracy
            best_t = 0.55

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
            'algo': 'xgboost_pipeline_v3'
        }, filepath)

    def load(self, filepath):
        if os.path.exists(filepath):
            try:
                data = joblib.load(filepath)
                if isinstance(data, dict):
                    self.model = data['model']
                    self.trained_at = data.get('trained_at')
                    self.best_threshold = data.get('threshold', 0.62)
                else:
                    self.model = data
                self.is_trained = True
                return True
            except: pass
        return False
