import asyncio
import pandas as pd
import time
import os
import json
from deriv_api import DerivAPI

DAYS = 90
CHUNK = 5000  # Max candles per Deriv API request

async def fetch_candles(api, symbol, granularity, days):
    target_start = int(time.time()) - days * 24 * 3600
    all_candles = []
    end_epoch = 'latest'

    label = f"{granularity}s"
    print(f"[{symbol}] Fetching {days}-day {label} history...")

    while True:
        params = {
            'ticks_history': symbol,
            'style': 'candles',
            'granularity': granularity,
            'count': CHUNK,
        }
        if end_epoch == 'latest':
            params['end'] = 'latest'
        else:
            params['end'] = end_epoch

        try:
            res = await asyncio.wait_for(api.ticks_history(params), timeout=30)
        except asyncio.TimeoutError:
            print(f"  Timeout fetching chunk. Retrying...")
            await asyncio.sleep(3)
            continue

        candles = res.get('candles', [])
        if not candles:
            print(f"  No more candles returned.")
            break

        all_candles = candles + all_candles
        earliest = int(candles[0]['epoch'])
        print(f"  Got {len(candles)} candles. Earliest: {earliest} | Target: {target_start} | Total so far: {len(all_candles)}")

        if earliest <= target_start:
            break

        end_epoch = earliest - 1
        await asyncio.sleep(0.5)  # Rate limit

    df = pd.DataFrame(all_candles)
    for col in ['epoch', 'open', 'high', 'low', 'close']:
        df[col] = pd.to_numeric(df[col])
    df = df.drop_duplicates('epoch').sort_values('epoch').reset_index(drop=True)

    # Trim to exactly 90 days
    df = df[df['epoch'] >= target_start]
    return df

async def main():
    with open('config.json') as f:
        cfg = json.load(f)

    api_token = cfg['api_token']
    app_id = cfg.get('app_id', '62845')

    os.makedirs('data', exist_ok=True)

    api = DerivAPI(app_id=app_id)
    print("Connecting to Deriv API...")
    auth = await asyncio.wait_for(api.authorize(api_token), timeout=15)
    print(f"Authorized: {auth['authorize']['loginid']}")

    symbols = ['BOOM500', 'CRASH500']

    for symbol in symbols:
        # 1-minute candles
        df_1m = await fetch_candles(api, symbol, granularity=60, days=DAYS)
        path_1m = f'data/{symbol}_60s_90d.csv'
        df_1m.to_csv(path_1m, index=False)
        print(f"[{symbol}] Saved {len(df_1m)} 1m candles -> {path_1m}")

        await asyncio.sleep(1)

        # 15-minute candles
        df_15m = await fetch_candles(api, symbol, granularity=900, days=DAYS)
        path_15m = f'data/{symbol}_900s_90d.csv'
        df_15m.to_csv(path_15m, index=False)
        print(f"[{symbol}] Saved {len(df_15m)} 15m candles -> {path_15m}")

        await asyncio.sleep(1)

    await api.disconnect()
    print("\nAll data fetched successfully.")

if __name__ == "__main__":
    asyncio.run(main())
