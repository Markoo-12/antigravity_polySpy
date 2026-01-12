"""
Quick debug to check if we can see any logs from CTF Exchange.
"""
import asyncio
from web3 import AsyncWeb3
from web3.providers import WebSocketProvider
from dotenv import load_dotenv
import os

load_dotenv()

CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
ORDER_FILLED_TOPIC = "0x2a4cc85b6db7ab7e0e9bcbfa093fc3ff1f89e48a1c552b1e27322ee8f66e4c86"

async def main():
    wss = os.getenv("POLYGON_WSS_URL")
    print(f"Using: {wss[:50]}...", flush=True)
    
    async with AsyncWeb3(WebSocketProvider(wss)) as w3:
        block = await w3.eth.block_number
        print(f"Current block: {block}", flush=True)
        
        # Test 1: Get ANY logs from CTF contract (last 10 blocks)
        print(f"\nTest 1: Any logs from CTF contract (blocks {block-10} to {block})...", flush=True)
        try:
            logs = await w3.eth.get_logs({
                "address": CTF_EXCHANGE,
                "fromBlock": block - 10,
                "toBlock": block,
            })
            print(f"Found {len(logs)} total logs from CTF contract", flush=True)
            for i, log in enumerate(logs[:5]):
                topic0 = log["topics"][0].hex() if log["topics"] else "none"
                print(f"  Log {i+1}: Topic0 = {topic0[:20]}...", flush=True)
        except Exception as e:
            print(f"Error: {e}", flush=True)
        
        # Test 2: Check specific OrderFilled topic
        print(f"\nTest 2: OrderFilled events specifically (last 10 blocks)...", flush=True)
        try:
            logs = await w3.eth.get_logs({
                "address": CTF_EXCHANGE,
                "topics": [ORDER_FILLED_TOPIC],
                "fromBlock": block - 10,
                "toBlock": block,
            })
            print(f"Found {len(logs)} OrderFilled events", flush=True)
        except Exception as e:
            print(f"Error: {e}", flush=True)
        
        # Test 3: Check if maybe the CTF contract is different (NegRisk?)
        print(f"\nTest 3: Checking NegRisk CTF contract instead...", flush=True)
        NEG_RISK_CTF = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
        try:
            logs = await w3.eth.get_logs({
                "address": NEG_RISK_CTF,
                "fromBlock": block - 10,
                "toBlock": block,
            })
            print(f"Found {len(logs)} logs from NegRisk CTF", flush=True)
        except Exception as e:
            print(f"Error: {e}", flush=True)
        
        # Test 4: Check the ACTUAL Polymarket exchange contract
        print(f"\nTest 4: Checking alternative exchange addresses...", flush=True)
        ALT_EXCHANGE = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"  # CTF token contract
        try:
            logs = await w3.eth.get_logs({
                "address": ALT_EXCHANGE,
                "fromBlock": block - 10,
                "toBlock": block,
            })
            print(f"Found {len(logs)} logs from CTF Token contract", flush=True)
        except Exception as e:
            print(f"Error: {e}", flush=True)

asyncio.run(main())
