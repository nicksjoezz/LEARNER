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
            # Move heavy I/O to a thread
            df = await asyncio.to_thread(pd.read_csv, filepath)
            if not df.empty:
                df = await asyncio.to_thread(lambda d: d.drop_duplicates(subset=['epoch']).sort_values('epoch'), df)
        except Exception as e:
            sys.stderr.write(f"Error reading {filepath}: {e}. Starting fresh.\n")

    now_ts = int(datetime.utcnow().timestamp())
    start_ts = int((datetime.utcnow() - timedelta(days=fetch_days)).timestamp())

    # Find Gaps
    def find_gaps(epochs, start, end, gran):
        if not epochs:
            return [(start, end)]

        gaps = []
        # Gap at the beginning
        if epochs[0] > start + gran:
            gaps.append((start, epochs[0] - 1))

        # Gaps between candles
        for i in range(len(epochs) - 1):
            if epochs[i+1] - epochs[i] > gran * 1.5: # Allow some jitter
                gaps.append((epochs[i] + 1, epochs[i+1] - 1))

        # Gap at the end
        if end - epochs[-1] > gran:
            gaps.append((epochs[-1] + 1, end))

        return gaps

    api = DerivAPI(app_id=app_id)

    async def fill_gap(gap_start, gap_end, total_remaining_tracker):
        nonlocal df
        current_end = gap_end
        empty_batches = 0
        retry_count = 0
        MAX_RETRIES = 3
        BATCH_SIZE = 5000 # Back to 5000 as requested
        save_counter = 0

        while current_end > gap_start:
            # sys.stderr.write(f"[{symbol}] Syncing gap: {datetime.utcfromtimestamp(gap_start)} to {datetime.utcfromtimestamp(current_end)}\n")

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
                    sys.stderr.write(f"API Error ({err.get('code')}): {err.get('message')}\n")

                    if err.get('code') == 'RateLimit':
                        await asyncio.sleep(60)
                        continue

                    retry_count += 1
                    if retry_count >= MAX_RETRIES:
                        sys.stderr.write(f"[{symbol}] Max retries reached for segment. Skipping...\n")
                        current_end -= granularity * BATCH_SIZE * 2 # Skip a block
                        retry_count = 0
                        continue

                    await asyncio.sleep(5)
                    continue

                candles = response.get('candles', [])
                if not candles:
                    sys.stderr.write(f"[{symbol}] No more data returned in this range.\n")
                    empty_batches += 1
                    if empty_batches >= 3:
                        break
                    current_end -= granularity * BATCH_SIZE
                    continue

                df_new = pd.DataFrame(candles)

                # Check for progress
                overlap = await asyncio.to_thread(lambda: df_new['epoch'].isin(df['epoch']).sum() if not df.empty else 0)
                num_new = len(df_new) - overlap

                if num_new > 0:
                    def update_df(old_df, new_df, ts):
                        d = pd.concat([old_df, new_df]).drop_duplicates(subset=['epoch']).sort_values('epoch')
                        return d[d['epoch'] >= ts]

                    df = await asyncio.to_thread(update_df, df, df_new, start_ts)

                    # Buffered writing to disk (every 4 batches)
                    save_counter += 1
                    if save_counter >= 4:
                        await asyncio.to_thread(df.to_csv, filepath, index=False)
                        save_counter = 0

                    total_remaining_tracker['total'] -= len(df_new)
                    if total_remaining_tracker['total'] < 0: total_remaining_tracker['total'] = 0
                    sys.stderr.write(f"[{symbol}] {len(df_new)} fetched. Remaining: {total_remaining_tracker['total']}\n")

                    empty_batches = 0
                    retry_count = 0
                else:
                    empty_batches += 1
                    sys.stderr.write(f"[{symbol}] Batch contained no new data ({empty_batches}/3).\n")
                    if empty_batches >= 3:
                        break

                batch_earliest = int(df_new.iloc[0]['epoch'])
                if batch_earliest <= gap_start:
                    break
                current_end = batch_earliest - 1

                # Slight delay to respect rate limits
                await asyncio.sleep(0.5)

            except asyncio.TimeoutError:
                sys.stderr.write(f"[{symbol}] Timeout during fetch. Retrying...\n")
                retry_count += 1
                if retry_count >= MAX_RETRIES:
                    sys.stderr.write(f"[{symbol}] Timeout retry limit reached. Skipping range.\n")
                    current_end -= granularity * BATCH_SIZE * 2
                    retry_count = 0
                else:
                    await asyncio.sleep(5)
            except Exception as e:
                sys.stderr.write(f"[{symbol}] Fetch error: {type(e).__name__}: {e}. Retrying in 5s...\n")
                retry_count += 1
                if retry_count >= MAX_RETRIES:
                    sys.stderr.write(f"[{symbol}] Exception retry limit reached. Skipping range.\n")
                    current_end -= granularity * BATCH_SIZE * 2
                    retry_count = 0
                else:
                    await asyncio.sleep(5)

        # Final save after gap is filled
        if not df.empty:
            await asyncio.to_thread(df.to_csv, filepath, index=False)

    # Calculate gaps based on target window
    epochs_list = df['epoch'].tolist() if not df.empty else []
    all_gaps = find_gaps(epochs_list, start_ts, now_ts, granularity)

    if not all_gaps:
        sys.stderr.write(f"[{symbol}] Data is up to date.\n")
    else:
        total_missing = sum((g_end - g_start) // granularity for g_start, g_end in all_gaps)
        sys.stderr.write(f"[{symbol}] Found {len(all_gaps)} gaps. Total missing: approx {total_missing} candles.\n")

        tracker = {'total': total_missing}
        for g_start, g_end in all_gaps:
            await fill_gap(g_start, g_end, tracker)

    try:
        await asyncio.wait_for(api.disconnect(), timeout=5)
    except:
        pass

    sys.stderr.write(f"Completed incremental update for {symbol}. Total: {len(df)} candles.\n")
    return df

async def main():
    symbols = ['R_100', 'R_75', 'R_50', 'R_25', 'R_10']
    for symbol in symbols:
        await update_symbol_data(symbol)

if __name__ == "__main__":
    asyncio.run(main())
