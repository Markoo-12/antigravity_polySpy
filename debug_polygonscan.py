"""Debug PolygonScan API directly."""
import asyncio
import aiohttp
from src.config import POLYGONSCAN_API_KEY, POLYGONSCAN_BASE_URL, POLYGON_CHAIN_ID


async def main():
    print(f"API Key: {POLYGONSCAN_API_KEY[:15]}..." if POLYGONSCAN_API_KEY else "NO API KEY")
    print(f"Base URL: {POLYGONSCAN_BASE_URL}")
    print(f"Chain ID: {POLYGON_CHAIN_ID}")
    
    proxy = "0x40e1D00D3A43aF1C4f215bD7A1039cc792AD973f"
    
    params = {
        "chainid": POLYGON_CHAIN_ID,
        "module": "contract",
        "action": "getcontractcreation",
        "contractaddresses": proxy,
        "apikey": POLYGONSCAN_API_KEY,
    }
    
    print(f"\nTesting contract creation lookup for: {proxy}")
    
    async with aiohttp.ClientSession() as session:
        async with session.get(POLYGONSCAN_BASE_URL, params=params) as resp:
            print(f"\nHTTP Status: {resp.status}")
            data = await resp.json()
            print(f"Response: {data}")


if __name__ == "__main__":
    asyncio.run(main())
