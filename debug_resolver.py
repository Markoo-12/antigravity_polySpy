"""Debug script to test address resolution on failing proxies."""
import asyncio
import sqlite3
from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider

from src.config import POLYGON_HTTP_URL
from src.demasker import AddressResolver
from src.demasker.proxy_detector import ProxyType


async def main():
    print("=" * 70)
    print("ADDRESS RESOLVER DEBUG")
    print("=" * 70)
    
    # Connect to database
    conn = sqlite3.connect(r'data/trades.db')
    c = conn.cursor()
    
    # Get proxy addresses where owner is None (failed resolution)
    c.execute('''
        SELECT DISTINCT proxy_address, amount_usdc 
        FROM trades 
        WHERE owner_address IS NULL 
        AND amount_usdc > 50000
        ORDER BY amount_usdc DESC
        LIMIT 10
    ''')
    
    failed_proxies = c.fetchall()
    conn.close()
    
    if not failed_proxies:
        print("No failed proxy resolutions found!")
        return
    
    print(f"\nFound {len(failed_proxies)} whale trade proxies with owner=None")
    print("-" * 70)
    
    # Create resolver
    w3 = AsyncWeb3(AsyncHTTPProvider(POLYGON_HTTP_URL))
    resolver = AddressResolver(w3)
    
    # Test each proxy
    for proxy_addr, amount in failed_proxies:
        print(f"\n[TEST] Proxy: {proxy_addr}")
        print(f"       Trade Amount: ${amount:,.0f}")
        
        try:
            # Step 1: Detect proxy type
            proxy_type = await resolver.detector.detect_type(proxy_addr)
            print(f"       Proxy Type: {proxy_type.value}")
            
            # Step 2: Get code
            code = await w3.eth.get_code(proxy_addr)
            code_len = len(code.hex()) if code else 0
            print(f"       Code Length: {code_len} chars")
            
            # Step 3: Try resolution
            owner, ptype = await resolver.resolve(proxy_addr)
            print(f"       Resolved Owner: {owner}")
            print(f"       Resolved Type: {ptype.value}")
            
            # Step 4: Additional diagnostics if still None
            if owner is None:
                print(f"       [DEBUG] Trying manual resolution...")
                
                # Try getting storage slots
                for slot in [0, 1, 2]:
                    storage = await w3.eth.get_storage_at(proxy_addr, slot)
                    storage_hex = storage.hex() if storage else "empty"
                    potential_addr = "0x" + storage_hex[-40:] if len(storage_hex) >= 40 else "N/A"
                    print(f"       Storage slot {slot}: {storage_hex[:20]}... -> {potential_addr}")
                
                # Try calling owner() directly
                try:
                    result = await w3.eth.call({
                        "to": proxy_addr,
                        "data": "0x8da5cb5b",  # owner()
                    })
                    print(f"       owner() raw result: {result.hex()}")
                except Exception as e:
                    print(f"       owner() call failed: {e}")
                
                # Try calling getOwners() directly  
                try:
                    result = await w3.eth.call({
                        "to": proxy_addr,
                        "data": "0xa0e67e2b",  # getOwners()
                    })
                    print(f"       getOwners() raw result: {result.hex()[:100]}...")
                except Exception as e:
                    print(f"       getOwners() call failed: {e}")
                    
        except Exception as e:
            print(f"       [ERROR] {e}")
            import traceback
            traceback.print_exc()
    
    print("\n" + "=" * 70)
    print("DEBUG COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
