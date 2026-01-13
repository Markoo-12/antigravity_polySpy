"""Check creation transaction of proxy to find owner."""
import asyncio
from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider
from src.config import POLYGON_HTTP_URL

async def main():
    w3 = AsyncWeb3(AsyncHTTPProvider(POLYGON_HTTP_URL))
    
    proxy = "0x40e1D00D3A43aF1C4f215bD7A1039cc792AD973f"
    
    # Known Polymarket proxy factory addresses
    FACTORY_ADDRESSES = [
        "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",  # Polymarket Proxy Factory
    ]
    
    print(f"Proxy: {proxy}")
    print("=" * 60)
    
    # Get the first transaction to this address
    # This requires tracing or API call - let's use the transaction hash from our DB
    import sqlite3
    conn = sqlite3.connect(r'data/trades.db')
    c = conn.cursor()
    c.execute('SELECT tx_hash FROM trades WHERE proxy_address = ? LIMIT 1', (proxy,))
    row = c.fetchone()
    
    if row:
        tx_hash = row[0]
        print(f"\nFound trade tx: 0x{tx_hash}")
        
        # Get transaction details
        tx = await w3.eth.get_transaction(f"0x{tx_hash}")
        print(f"From: {tx['from']}")
        print(f"To: {tx['to']}")
        print(f"Value: {tx['value']}")
        
        # Get transaction logs
        receipt = await w3.eth.get_transaction_receipt(f"0x{tx_hash}")
        print(f"\nLogs ({len(receipt['logs'])}):")
        for i, log in enumerate(receipt['logs']):
            print(f"  Log {i}: Address={log['address']}")
            print(f"         Topics={[t.hex() for t in log['topics'][:2]]}")
    
    conn.close()
    
    # Alternative: try known Polymarket patterns
    # Some Polymarket proxies are just EIP-1167 clones that delegate
    # but the "owner" is encoded in the creation transaction input
    print("\n" + "=" * 60)
    print("Trying alternative detection methods:")
    
    # Check nonce and first transaction
    nonce = await w3.eth.get_transaction_count(proxy)
    print(f"Proxy nonce (transactions out): {nonce}")
    
    # Check balance
    balance = await w3.eth.get_balance(proxy)
    print(f"Proxy MATIC balance: {balance}")

if __name__ == "__main__":
    asyncio.run(main())
