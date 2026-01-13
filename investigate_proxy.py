"""Investigate the implementation contract to find owner storage layout."""
import asyncio
from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider
from src.config import POLYGON_HTTP_URL

# Common function selectors
SELECTORS = {
    "owner": "0x8da5cb5b",            # owner()
    "getOwner": "0x893d20e8",         # getOwner()
    "admin": "0xf851a440",            # admin()
    "getAdmin": "0x6e9960c3",         # getAdmin()
    "implementation": "0x5c60da1b",   # implementation()
    "getWalletOwner": "0x7f0d5e2c",   # getWalletOwner() - custom PM
    "user": "0x4f8632ba",             # user()
    "signer": "0x238ac933",           # signer()
}

async def main():
    w3 = AsyncWeb3(AsyncHTTPProvider(POLYGON_HTTP_URL))
    
    proxy = "0x40e1D00D3A43aF1C4f215bD7A1039cc792AD973f"
    implementation = "0x44e999D5c2F66Ef0861317f9a4805ac2e90aeb4f"  # checksummed
    
    print(f"Testing proxy: {proxy}")
    print(f"Implementation: {implementation}")
    print("=" * 60)
    
    # Test each selector on the PROXY (delegates to impl)
    print("\n1. Testing function calls on PROXY:")
    for name, selector in SELECTORS.items():
        try:
            result = await w3.eth.call({
                "to": w3.to_checksum_address(proxy),
                "data": selector,
            })
            if result and result != b'':
                potential_addr = "0x" + result.hex()[-40:]
                print(f"   {name}(): {result.hex()} -> {potential_addr}")
        except Exception as e:
            print(f"   {name}(): FAILED - {str(e)[:50]}")
    
    # Check multiple storage slots
    print("\n2. Checking proxy storage slots:")
    for slot in range(20):
        storage = await w3.eth.get_storage_at(proxy, slot)
        storage_hex = storage.hex()
        if storage_hex != "00" * 32:
            potential_addr = "0x" + storage_hex[-40:]
            print(f"   Slot {slot}: {storage_hex}")
            print(f"         -> potential address: {potential_addr}")
    
    # Get the creation transaction of the proxy
    print("\n3. Checking proxy creation...")
    try:
        # Get first transaction of proxy (may need different approach)
        # For now, just verify it exists
        code = await w3.eth.get_code(proxy)
        print(f"   Proxy bytecode length: {len(code.hex())} chars")
    except Exception as e:
        print(f"   Error: {e}")
    
    # Check implementation bytecode to understand its interface
    print("\n4. Implementation code analysis:")
    impl_code = await w3.eth.get_code(w3.to_checksum_address(implementation))
    print(f"   Implementation bytecode length: {len(impl_code.hex())} chars")
    
    # Check if it contains certain selector patterns
    impl_hex = impl_code.hex()
    for name, selector in SELECTORS.items():
        if selector[2:] in impl_hex:
            print(f"   Found selector for {name}()")

if __name__ == "__main__":
    asyncio.run(main())
