"""
Test HTTP endpoint to verify connectivity and event topic.
"""
import requests
from dotenv import load_dotenv
import os

load_dotenv()

# Convert WSS to HTTPS
wss = os.getenv("POLYGON_WSS_URL", "")
https = wss.replace("wss://", "https://").replace("ws://", "http://")

print(f"Testing HTTP: {https[:50]}...", flush=True)

CTF = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
TOPIC = "0xd0a08e8c493f9c94f29311604c9de1473f33a458b318ccd0c1a6dc68a49eb59e"

# Get block number first
r = requests.post(https, json={"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}, timeout=10)
block_hex = r.json()["result"]
block = int(block_hex, 16)
print(f"Current block: {block}", flush=True)

# Get logs from single block
r = requests.post(https, json={
    "jsonrpc":"2.0","id":2,"method":"eth_getLogs",
    "params":[{
        "address": CTF,
        "topics": [TOPIC],
        "fromBlock": hex(block),
        "toBlock": hex(block)
    }]
}, timeout=10)
data = r.json()
if "error" in data:
    print(f"Error: {data['error']}", flush=True)
else:
    logs = data.get("result", [])
    print(f"Found {len(logs)} OrderFilled events in block {block}!", flush=True)
    
    for i, log in enumerate(logs[:3]):
        tx = log.get("transactionHash", "")[:20]
        print(f"  Event {i+1}: tx={tx}...", flush=True)
