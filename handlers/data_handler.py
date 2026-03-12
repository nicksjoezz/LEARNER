import asyncio
import pandas as pd
from deriv_api import DerivAPI
import os
from datetime import datetime, timedelta
import sys
from config_utils import load_config

class DataHandler:
    def __init__(self, data_dir='data'):
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)

    async def update_symbol_data(self, symbol):
        config = load_config()
        fetch_days = int(config.get('fetch_days', 365))
        app_id = config.get('app_id')
        filepath = os.path.join(self.data_dir, f"{symbol}_5m_2y.csv")
        granularity = 300

        df = pd.DataFrame()
        if os.path.exists(filepath):
            try:
                df = await asyncio.to_thread(pd.read_csv, filepath)
                if not df.empty:
                    df = await asyncio.to_thread(lambda d: d.drop_duplicates(subset=['epoch']).sort_values('epoch'), df)
            except Exception as e:
                sys.stderr.write(f"Error reading {filepath}: {e}. Starting fresh.\n")

        now_ts = int(datetime.utcnow().timestamp())
        start_ts = int((datetime.utcnow() - timedelta(days=fetch_days)).timestamp())

        def find_gaps(epochs, start, end, gran):
            if not epochs:
                return [(start, end)]
            gaps = []
            if epochs[0] > start + gran:
                gaps.append((start, epochs[0] - 1))
            for i in range(len(epochs) - 1):
                if epochs[i+1] - epochs[i] > gran * 1.5:
                    gaps.append((epochs[i] + 1, epochs[i+1] - 1))
            if end - epochs[-1] > gran:
                gaps.append((epochs[-1] + 1, end))
            return gaps

        epochs_list = df['epoch'].tolist() if not df.empty else []
        all_gaps = find_gaps(epochs_list, start_ts, now_ts, granularity)

        if not all_gaps:
            sys.stderr.write(f"[{symbol}] Data is up to date.\n")
            return df

        total_missing = sum((g_end - g_start) // granularity for g_start, g_end in all_gaps)
        sys.stderr.write(f"[{symbol}] Total missing: approx {total_missing} candles.\n")

        tracker = {'total': total_missing}
        api = DerivAPI(app_id=app_id)

        try:
            for g_start, g_end in all_gaps:
                df = await self._fill_gap(api, symbol, df, g_start, g_end, granularity, start_ts, filepath, tracker)
        except Exception as e:
            sys.stderr.write(f"[{symbol}] Critical error during sync: {e}\n")
        finally:
            try:
                await asyncio.wait_for(api.disconnect(), timeout=10)
            except: pass

        sys.stderr.write(f"Completed incremental update for {symbol}. Total: {len(df)} candles.\n")
        return df

    async def _fill_gap(self, api, symbol, df, gap_start, gap_end, granularity, start_ts, filepath, tracker):
        current_end = gap_end
        empty_batches = 0
        retry_count = 0
        MAX_RETRIES = 3
        BATCH_SIZE = 5000
        save_counter = 0

        while current_end > gap_start:
            try:
                response = await asyncio.wait_for(api.ticks_history({
                    'ticks_history': symbol,
                    'end': str(current_end),
                    'adjust_start_time': 1,
                    'count': BATCH_SIZE,
                    'granularity': granularity,
                    'style': 'candles'
                }), timeout=60)

                if 'error' in response:
                    err = response['error']
                    sys.stderr.write(f"[{symbol}] API Error: {err.get('message')}\n")
                    if err.get('code') == 'RateLimit':
                        await asyncio.sleep(60)
                        continue
                    retry_count += 1
                    if retry_count >= MAX_RETRIES:
                        sys.stderr.write(f"[{symbol}] Max retries reached for segment. Skipping...\n")
                        current_end -= granularity * BATCH_SIZE
                        retry_count = 0
                        continue
                    await asyncio.sleep(5)
                    continue

                candles = response.get('candles', [])
                if not candles:
                    empty_batches += 1
                    sys.stderr.write(f"[{symbol}] No data returned ({empty_batches}/3).\n")
                    if empty_batches >= 3: break
                    current_end -= granularity * BATCH_SIZE
                    continue

                df_new = pd.DataFrame(candles)
                overlap = await asyncio.to_thread(lambda: df_new['epoch'].isin(df['epoch']).sum() if not df.empty else 0)
                num_new = len(df_new) - overlap

                if num_new > 0:
                    def update_df(old_df, new_df, ts):
                        d = pd.concat([old_df, new_df]).drop_duplicates(subset=['epoch']).sort_values('epoch')
                        return d[d['epoch'] >= ts]

                    df = await asyncio.to_thread(update_df, df, df_new, start_ts)

                    save_counter += 1
                    if save_counter >= 4:
                        await asyncio.to_thread(df.to_csv, filepath, index=False)
                        save_counter = 0

                    num_downloaded = len(df_new)
                    tracker['total'] -= num_downloaded
                    if tracker['total'] < 0: tracker['total'] = 0
                    sys.stderr.write(f"{num_downloaded} fetched remaining {tracker['total']}\n")

                    empty_batches = 0
                    retry_count = 0

                    batch_earliest = int(df_new.iloc[0]['epoch'])
                    if batch_earliest <= gap_start: break
                    current_end = batch_earliest - 1
                else:
                    empty_batches += 1
                    sys.stderr.write(f"[{symbol}] Batch contained no new data ({empty_batches}/3).\n")
                    if empty_batches >= 3: break
                    current_end -= granularity * BATCH_SIZE

                await asyncio.sleep(0.5)

            except asyncio.TimeoutError:
                sys.stderr.write(f"[{symbol}] Timeout during fetch. Retrying...\n")
                retry_count += 1
                if retry_count >= MAX_RETRIES:
                    current_end -= granularity * BATCH_SIZE
                    retry_count = 0
                else:
                    await asyncio.sleep(5)
            except Exception as e:
                sys.stderr.write(f"[{symbol}] Fetch error: {e}. Retrying...\n")
                retry_count += 1
                if retry_count >= MAX_RETRIES:
                    current_end -= granularity * BATCH_SIZE
                    retry_count = 0
                else:
                    await asyncio.sleep(5)

        if not df.empty:
            await asyncio.to_thread(df.to_csv, filepath, index=False)
        return df
