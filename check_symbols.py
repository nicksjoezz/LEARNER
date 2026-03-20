import asyncio
from deriv_api import DerivAPI
import json

async def check_symbols():
    api = DerivAPI(app_id='1089')
    try:
        response = await api.active_symbols({"active_symbols": "brief", "product_type": "basic"})
        symbols = response.get('active_symbols', [])
        for s in symbols:
            if 'Crash' in s['display_name'] or 'Boom' in s['display_name']:
                print(f"Symbol: {s['symbol']}, Name: {s['display_name']}")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await api.disconnect()

if __name__ == "__main__":
    asyncio.run(check_symbols())
