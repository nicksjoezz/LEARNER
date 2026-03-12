import asyncio
from handlers.data_handler import DataHandler

async def update_symbol_data(symbol, data_dir='data'):
    handler = DataHandler(data_dir=data_dir)
    return await handler.update_symbol_data(symbol)

async def main():
    symbols = ['R_100', 'R_75', 'R_50', 'R_25', 'R_10']
    for symbol in symbols:
        await update_symbol_data(symbol)

if __name__ == "__main__":
    asyncio.run(main())
