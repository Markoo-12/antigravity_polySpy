"""
Test the corrected topic signature.
"""
import asyncio
from web3 import AsyncWeb3
from web3.providers import WebSocketProvider
from dotenv import load_dotenv
import os

load_dotenv()

CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
NEW_TOPIC = "0xd0a08e8c493f9c94f29311604c9de1473f33a458b318ccd0c1a6dc68a49eb59e"

async def main():
    wss = os.getenv("POLYGON_WSS_URL")
    print(f"Using: {wss[:40]}...", flush=True)
    
    async with AsyncWeb3(WebSocketProvider(wss)) as w3:
        block = await w3.eth.block_number
        print(f"Checking block {block} with NEW topic...", flush=True)
        
        # Test with topic filter
        logs = await w3.eth.get_logs({
            "address": CTF_EXCHANGE,
            "topics": [NEW_TOPIC],
            "fromBlock": block,
            "toBlock": block,
        })
        
        print(f"Found {len(logs)} OrderFilled events with topic filter!", flush=True)
        
        # Also check without topic filter
        all_logs = await w3.eth.get_logs({
            "address": CTF_EXCHANGE,
            "fromBlock": block,
            "toBlock": block,
        })
        
        print(f"Found {len(all_logs)} TOTAL events from contract", flush=True)
        
        # Count matches
        matching = sum(1 for log in all_logs if log["topics"] and log["topics"][0].hex() == NEW_TOPIC)
        print(f"Of those, {matching} match our topic", flush=True)
        
        if matching != len(logs):
            print("MISMATCH! Comparing topic hashes...", flush=True)
            for log in all_logs[:3]:
                actual = log["topics"][0].hex() if log["topics"] else "none"
                match = "YES" if actual == NEW_TOPIC else "NO"
                print(f"  {actual} -> {match}", flush=True)

asyncio.run(main())
