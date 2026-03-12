import asyncio
import pandas as pd
from deriv_api import DerivAPI
import os
import json
from datetime import datetime, timedelta
import sys
from config_utils import load_config

async def update_symbol_data(symbol, data_dir='data'):
    config = load_config()
    fetch_days = int(config.get('fetch_days', 365))
    app_id = config.get('app_id')
    filepath = os.path.join(data_dir, f"{symbol}_5m_2y.csv")
    granularity = 300
    os.makedirs(data_dir, exist_ok=True)

    df = pd.DataFrame()
    if os.path.exists(filepath):
        try:
            df = await asyncio.to_thread(pd.read_csv, filepath)
            if not df.empty:
                df = await asyncio.to_thread(lambda: df.drop_duplicates(subset=['epoch']).sort_values('epoch'))
        except Exception as e:
            sys.stderr.write(f"Error reading {filepath}: {e}. Starting fresh.\n")

    now_ts = int(datetime.utcnow().timestamp())
    start_ts = int((datetime.utcnow() - timedelta(days=fetch_days)).timestamp())

    api = DerivAPI(app_id=app_id)

    async def fetch_range(current_start, current_end):
        nonlocal df
        retry_count = 0
        MAX_RETRIES = 3
        batch_count = 0

        while current_end > current_start:
            sys.stderr.write(f"[{symbol}] Syncing gap ending at {datetime.utcfromtimestamp(current_end)}\n")

            try:
                # Reduced batch size to 2500 for better reliability
                response = await asyncio.wait_for(api.ticks_history({
                    'ticks_history': symbol,
                    'end': str(current_end),
                    'adjust_start_time': 1,
                    'count': 2500,
                    'granularity': granularity,
                    'style': 'candles'
                }), timeout=60)

                if 'error' in response:
                    err = response['error']
                    sys.stderr.write(f"API Error ({err.get('code')}): {err.get('message')}\n")

                    if err.get('code') == 'RateLimit':
                        await asyncio.sleep(60)
                        continue

                    retry_count += 1
                    if retry_count >= MAX_RETRIES:
                        sys.stderr.write(f"[{symbol}] Skipping segment due to persistent API error...\n")
                        current_end -= granularity * 2500
                        retry_count = 0
                        continue

                    await asyncio.sleep(5)
                    continue

                candles = response.get('candles', [])
                if not candles:
                    sys.stderr.write(f"[{symbol}] Empty response. Skipping Segment.\n")
                    current_end -= granularity * 2500
                    continue

                df_new = pd.DataFrame(candles)

                # Progress check
                overlap_mask = await asyncio.to_thread(lambda: df_new['epoch'].isin(df['epoch'])) if not df.empty else [False]*len(df_new)
                num_new = len(df_new) - sum(overlap_mask)

                if num_new > 0:
                    def merge_and_filter(old_df, new_df_raw):
                        merged = pd.concat([old_df, new_df_raw]).drop_duplicates(subset=['epoch']).sort_values('epoch')
                        return merged[merged['epoch'] >= start_ts]

                    df = await asyncio.to_thread(merge_and_filter, df, df_new)
                    sys.stderr.write(f"[{symbol}] Added {num_new} new candles.\n")

                    # Periodic save to disk
                    batch_count += 1
                    if batch_count >= 4: # Every ~10,000 candles
                        await asyncio.to_thread(df.to_csv, filepath, index=False)
                        batch_count = 0

                    retry_count = 0
                else:
                    sys.stderr.write(f"[{symbol}] No new data in batch. Skipping Segment.\n")
                    current_end -= granularity * 2500

                batch_earliest = int(df_new.iloc[0]['epoch'])
                # Force strictly decreasing current_end
                if batch_earliest < current_end:
                    current_end = batch_earliest - 1
                else:
                    current_end -= granularity * 2500

                if current_end <= current_start:
                    break

                await asyncio.sleep(0.2)

            except Exception as e:
                sys.stderr.write(f"Fetch error for {symbol}: {e}. Retrying...\n")
                retry_count += 1
                if retry_count >= MAX_RETRIES:
                    current_end -= granularity * 2500
                    retry_count = 0
                else:
                    await asyncio.sleep(10)

    # Save initial/existing data once
    if not df.empty:
        await asyncio.to_thread(df.to_csv, filepath, index=False)

    # Simple two-pass sync
    # 1. Forward (up to now)
    last_recorded = int(df['epoch'].max()) if not df.empty else start_ts
    if now_ts - last_recorded > granularity:
        await fetch_range(last_recorded, now_ts)

    # 2. Backward (historical)
    earliest_recorded = int(df['epoch'].min()) if not df.empty else now_ts
    if earliest_recorded > start_ts:
        await fetch_range(start_ts, earliest_recorded)

    # Final save
    await asyncio.to_thread(df.to_csv, filepath, index=False)

    try:
        await asyncio.wait_for(api.disconnect(), timeout=10)
    except:
        pass

    sys.stderr.write(f"Completed sync for {symbol}. Total: {len(df)} candles.\n")
    return df

async def main():
    symbols = ['R_100', 'R_75', 'R_50', 'R_25', 'R_10']
    for symbol in symbols:
        await update_symbol_data(symbol)

if __name__ == "__main__":
    asyncio.run(main())
