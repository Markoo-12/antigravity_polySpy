"""
Find the ACTUAL OrderFilled topic by examining real events.
"""
import requests
from dotenv import load_dotenv
import os

load_dotenv()

wss = os.getenv("POLYGON_WSS_URL", "")
https = wss.replace("wss://", "https://").replace("ws://", "http://")

print(f"Using: {https[:50]}...", flush=True)

CTF = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"

# Get current block
r = requests.post(https, json={"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}, timeout=10)
block = int(r.json()["result"], 16)
print(f"Block: {block}", flush=True)

# Get ALL logs from this block (no topic filter)
r = requests.post(https, json={
    "jsonrpc":"2.0","id":2,"method":"eth_getLogs",
    "params":[{
        "address": CTF,
        "fromBlock": hex(block),
        "toBlock": hex(block)
    }]
}, timeout=10)

data = r.json()
if "error" in data:
    print(f"Error: {data['error']}", flush=True)
else:
    logs = data.get("result", [])
    print(f"\nFound {len(logs)} TOTAL events in block {block}", flush=True)
    
    # Count by topic
    topics = {}
    for log in logs:
        t = log["topics"][0] if log.get("topics") else "none"
        topics[t] = topics.get(t, 0) + 1
    
    print(f"\nTopics breakdown:", flush=True)
    for t, count in sorted(topics.items(), key=lambda x: -x[1]):
        print(f"  {t} = {count} events", flush=True)
    
    # Show one full log for reference
    if logs:
        print(f"\nSample log:", flush=True)
        log = logs[0]
        print(f"  Topics: {log['topics']}", flush=True)
        print(f"  Data length: {len(log['data'])} chars", flush=True)
