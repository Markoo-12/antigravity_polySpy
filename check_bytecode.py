"""Check bytecode of a proxy."""
import asyncio
from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider
from src.config import POLYGON_HTTP_URL

async def main():
    w3 = AsyncWeb3(AsyncHTTPProvider(POLYGON_HTTP_URL))
    
    # Test proxy address ($200k trade)
    proxy = "0x40e1D00D3A43aF1C4f215bD7A1039cc792AD973f"
    
    code = await w3.eth.get_code(proxy)
    code_hex = code.hex()
    
    print(f"Proxy: {proxy}")
    print(f"Code length: {len(code_hex)} chars")
    print(f"Full bytecode: {code_hex}")
    print()
    
    # Check for known patterns
    if "363d3d373d3d3d363d73" in code_hex:
        print("Pattern: EIP-1167 Minimal Proxy (Clone)")
        # Extract implementation address
        # EIP-1167 format: 363d3d373d3d3d363d73<address>5af43d82803e903d91602b57fd5bf3
        start = code_hex.find("363d3d373d3d3d363d73") + 20
        impl_address = "0x" + code_hex[start:start+40]
        print(f"Implementation address: {impl_address}")
        
        # Try to fetch implementation bytecode
        impl_code = await w3.eth.get_code(impl_address)
        print(f"Implementation code length: {len(impl_code.hex())} chars")

if __name__ == "__main__":
    asyncio.run(main())
