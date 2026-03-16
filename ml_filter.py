import pandas as pd
import numpy as np
import xgboost as xgb
import joblib
import os
from datetime import datetime
from sklearn.model_selection import StratifiedKFold, cross_val_predict

class MLFilter:
    def __init__(self):
        # XGBoost parameters optimized for 5m Rise/Fall (v4 configuration)
        self.model = xgb.XGBClassifier(
            objective='binary:logistic',
            n_estimators=150,      # Reduced to further prevent overfitting on small signal sets
            max_depth=3,           # Shallower depth for better generalization
            learning_rate=0.03,    # Slower learning rate
            subsample=0.7,         # More aggressive subsampling
            colsample_bytree=0.7,
            min_child_weight=15,   # Increased floor to ensure leaf stability
            gamma=0.5,             # Higher regularization
            random_state=42,
            eval_metric='aucpr',
            scale_pos_weight=1.0,
            reg_alpha=0.1,         # L1 regularization
            reg_lambda=1.0         # L2 regularization
        )
        self.is_trained = False
        self.trained_at = None
        self.best_threshold = 0.65

        # Advanced Feature Set following the requested architecture (v7)
        self.feature_cols = [
            # RSI features
            'rsi7', 'rsi14', 'rsi7_slope', 'rsi7_strength', 'rsi_agreement',
            'bull_divergence', 'bear_divergence', 'rsi7_overbought', 'rsi7_oversold',
            # MACD features
            'macd_hist', 'macd_hist_rising', 'macd_hist_strength', 'macd_above_zero',
            'macd_cross_up', 'macd_cross_down', 'hist_acceleration',
            # Bollinger Bands features
            'bb20_position', 'bb10_position', 'bb20_width', 'volatility_expanding',
            'bb_squeeze', 'above_bb20_mid', 'bb_outside_upper', 'bb_outside_lower',
            # ATR features
            'atr14', 'atr_percentile', 'atr_ratio', 'candle_vs_atr', 'atr_expanding',
            'vol_dead', 'vol_extreme', 'vol_normal',
            # Stochastic features
            'stoch_k_fast', 'stoch_fast_bull', 'stoch_fast_bear', 'stoch_overbought',
            'stoch_oversold', 'stoch_agreement', 'stoch_slope',
            # CCI features
            'cci14', 'cci7', 'cci_cross_up', 'cci_cross_down', 'cci_break_up',
            'cci_break_down', 'cci_agreement', 'cci_extreme_up', 'cci_extreme_dn',
            # EMA Ribbon features
            'ema_bullish_stack', 'ema_bearish_stack', 'price_vs_ema3', 'price_vs_ema8',
            'price_vs_ema20', 'price_vs_ema50', 'ribbon_width', 'ema8_slope',
            'momentum_agrees',
            # Candle Pattern features
            'body_ratio', 'wick_ratio', 'is_bullish', 'candle_streak',
            'bull_engulf', 'bear_engulf', 'is_doji', 'gap',
            # UT Bot Internal features
            'ut_stop_dist', 'ut_above', 'ut_below',
            # Time & Meta features
            'hour', 'day_of_week', 'session',
            'rsi7_lag_1', 'macd_lag_1', 'close_change_lag_1'
        ]

    def prepare_features(self, df, positional_indices):
        valid_indices = [idx for idx in positional_indices if 0 <= idx < len(df)]
        if not valid_indices:
            return np.zeros((0, len(self.feature_cols)), dtype=np.float32)

        # Efficiently extract features with minimal copying
        feature_data = df.iloc[valid_indices][self.feature_cols].copy()
        feature_data = feature_data.fillna(0).astype(np.float32)
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

        # Explicitly clean up training predictions
        del train_preds

        if train_acc < 0.52:
            # If the model can't even fit the training data better than a coin flip, reject it.
            return False

        # --- Threshold Tuning Phase (using Out-of-Fold predictions) ---
        # This prevents the model from choosing a threshold based on "memorized" training data.
        skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
        try:
            # Get honest probabilities for training data
            oof_probs = cross_val_predict(self.model, X, y, cv=skf, method='predict_proba')[:, 1]
        except:
            # Fallback to simple predictions if CV fails
            oof_probs = self.model.predict_proba(X)[:, 1]

        best_utility = -1
        best_t = 0.58

        total_signals = len(y)
        # Target at least 45% retention to maintain activity
        participation_floor = total_signals * 0.45

        found_above_floor = False
        # Test thresholds from 52% to 75%
        for t in np.linspace(0.52, 0.75, 47):
            mask = oof_probs >= t
            subset_y = y[mask]
            num_trades = len(subset_y)

            if num_trades < participation_floor: continue

            found_above_floor = True
            win_rate = np.mean(subset_y)
            # Utility Score: Accuracy weight increased, but balanced by volume
            # We want at least 53% winrate to cover spread/commissions ideally
            utility = (win_rate - 0.51) * np.sqrt(num_trades)

            if utility > best_utility:
                best_utility = utility
                best_t = t

        if not found_above_floor:
            # PARTICIPATION FALLBACK:
            # If no threshold meets the 45% volume floor while maintaining target accuracy,
            # find the threshold that gets closest to the 45% floor regardless of accuracy.
            best_dist = 999
            for t in np.linspace(0.50, 0.60, 21):
                mask = oof_probs >= t
                num_trades = np.sum(mask)
                dist = abs(num_trades - participation_floor)
                if dist < best_dist:
                    best_dist = dist
                    best_t = t

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
