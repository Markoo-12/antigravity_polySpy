"""
Debug script to test WebSocket connection and event streaming.
Run this to diagnose why events aren't showing up.
"""
import asyncio
import sys
from datetime import datetime
from web3 import AsyncWeb3
from web3.providers import WebSocketProvider

# Force unbuffered output
sys.stdout.reconfigure(line_buffering=True)

# Load config
from dotenv import load_dotenv
import os
load_dotenv()

POLYGON_WSS_URL = os.getenv("POLYGON_WSS_URL", "")
CTF_EXCHANGE_ADDRESS = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
ORDER_FILLED_TOPIC = "0x2a4cc85b6db7ab7e0e9bcbfa093fc3ff1f89e48a1c552b1e27322ee8f66e4c86"


async def test_connection():
    """Test basic WebSocket connection."""
    print("=" * 60, flush=True)
    print("[TEST 1] Testing WebSocket Connection", flush=True)
    print("=" * 60, flush=True)
    print(f"WSS URL: {POLYGON_WSS_URL[:50]}...", flush=True)
    
    if not POLYGON_WSS_URL:
        print("[ERROR] POLYGON_WSS_URL is empty! Check your .env file", flush=True)
        return False
    
    try:
        async with AsyncWeb3(WebSocketProvider(POLYGON_WSS_URL)) as w3:
            chain_id = await w3.eth.chain_id
            block = await w3.eth.block_number
            print(f"[OK] Connected! Chain: {chain_id}, Block: {block}", flush=True)
            return True
    except Exception as e:
        print(f"[ERROR] Connection failed: {e}", flush=True)
        return False


async def test_historical_logs():
    """Test fetching recent historical logs."""
    print("\n" + "=" * 60, flush=True)
    print("[TEST 2] Fetching Recent Historical Logs", flush=True)
    print("=" * 60, flush=True)
    
    try:
        async with AsyncWeb3(WebSocketProvider(POLYGON_WSS_URL)) as w3:
            current_block = await w3.eth.block_number
            from_block = current_block - 100  # Last 100 blocks (~3-4 minutes)
            
            print(f"Fetching logs from block {from_block} to {current_block}...", flush=True)
            
            logs = await w3.eth.get_logs({
                "address": CTF_EXCHANGE_ADDRESS,
                "topics": [ORDER_FILLED_TOPIC],
                "fromBlock": from_block,
                "toBlock": current_block,
            })
            
            print(f"[DATA] Found {len(logs)} OrderFilled events in last 100 blocks", flush=True)
            
            if logs:
                for i, log in enumerate(logs[:5]):
                    print(f"  Log {i+1}: Block {log['blockNumber']}, TX {log['transactionHash'].hex()[:16]}...", flush=True)
            else:
                print("[WARN] No events found in recent blocks", flush=True)
                print("This could mean:", flush=True)
                print("  - No trades happened recently (unlikely)", flush=True)
                print("  - Contract address is wrong", flush=True)
                print("  - Topic signature is wrong", flush=True)
            
            return len(logs) > 0
    except Exception as e:
        print(f"[ERROR] Failed to fetch logs: {e}", flush=True)
        return False


async def test_subscription():
    """Test WebSocket subscription."""
    print("\n" + "=" * 60, flush=True)
    print("[TEST 3] Testing WebSocket Subscription (30 seconds)", flush=True)
    print("=" * 60, flush=True)
    
    try:
        async with AsyncWeb3(WebSocketProvider(POLYGON_WSS_URL)) as w3:
            print("Creating subscription...", flush=True)
            
            subscription_params = {
                "address": CTF_EXCHANGE_ADDRESS,
                "topics": [ORDER_FILLED_TOPIC],
            }
            
            try:
                subscription = await w3.eth.subscribe("logs", subscription_params)
                print("[OK] Subscription created! Waiting for events...", flush=True)
                print("(Press Ctrl+C to stop)", flush=True)
                
                event_count = 0
                start_time = datetime.now()
                timeout = 30  # seconds
                
                async for log in subscription:
                    event_count += 1
                    elapsed = (datetime.now() - start_time).total_seconds()
                    print(f"[EVENT {event_count}] Block {log.get('blockNumber', '?')} @ {elapsed:.1f}s", flush=True)
                    
                    if elapsed > timeout:
                        print(f"\n[OK] Received {event_count} events in {timeout}s", flush=True)
                        break
                
                return event_count > 0
                
            except Exception as sub_error:
                error_str = str(sub_error).lower()
                print(f"[WARN] Subscription error: {sub_error}", flush=True)
                
                if "not supported" in error_str or "subscription" in error_str:
                    print("[INFO] Your RPC endpoint doesn't support subscriptions", flush=True)
                    print("[INFO] Falling back to polling test...", flush=True)
                    return await test_polling(w3)
                else:
                    raise
                    
    except Exception as e:
        print(f"[ERROR] Subscription test failed: {e}", flush=True)
        return False


async def test_polling(w3: AsyncWeb3):
    """Test polling-based event capture."""
    print("\n[TEST 3b] Testing Polling Mode (30 seconds)", flush=True)
    
    try:
        event_filter = await w3.eth.filter({
            "address": CTF_EXCHANGE_ADDRESS,
            "topics": [ORDER_FILLED_TOPIC],
        })
        
        print("[OK] Filter created! Polling for events...", flush=True)
        
        event_count = 0
        start_time = datetime.now()
        timeout = 30
        
        while (datetime.now() - start_time).total_seconds() < timeout:
            events = await event_filter.get_new_entries()
            
            if events:
                for event in events:
                    event_count += 1
                    elapsed = (datetime.now() - start_time).total_seconds()
                    print(f"[EVENT {event_count}] Block {event.get('blockNumber', '?')} @ {elapsed:.1f}s", flush=True)
            
            await asyncio.sleep(1)
        
        print(f"\n[RESULT] Received {event_count} events in {timeout}s via polling", flush=True)
        return event_count > 0
        
    except Exception as e:
        print(f"[ERROR] Polling test failed: {e}", flush=True)
        return False


async def main():
    print("\n" + "#" * 60, flush=True)
    print("#  POLYMARKET INSIDER SENTINEL - DIAGNOSTICS", flush=True)
    print("#" * 60 + "\n", flush=True)
    
    # Test 1: Connection
    conn_ok = await test_connection()
    if not conn_ok:
        print("\n[FATAL] Cannot connect to WebSocket. Check POLYGON_WSS_URL in .env", flush=True)
        return
    
    # Test 2: Historical logs
    hist_ok = await test_historical_logs()
    
    # Test 3: Live subscription
    print("\nStarting live event test...", flush=True)
    sub_ok = await test_subscription()
    
    # Summary
    print("\n" + "=" * 60, flush=True)
    print("DIAGNOSTIC SUMMARY", flush=True)
    print("=" * 60, flush=True)
    print(f"Connection:     {'OK' if conn_ok else 'FAILED'}", flush=True)
    print(f"Historical:     {'OK' if hist_ok else 'NO EVENTS'}", flush=True)
    print(f"Live Events:    {'OK' if sub_ok else 'NO EVENTS'}", flush=True)
    
    if not hist_ok and not sub_ok:
        print("\n[DIAGNOSIS] No events detected. Possible causes:", flush=True)
        print("  1. RPC endpoint rate limited or blocking requests", flush=True)
        print("  2. WebSocket connection issues", flush=True)
        print("  3. Try a different RPC (QuickNode, Infura, etc.)", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
