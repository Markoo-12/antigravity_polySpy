"""
Wallet Investigation Script
Analyzes a specific wallet address using Moralis API
"""
import asyncio
import aiohttp
import os
import json
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

WALLET_ADDRESS = "0x31a56e9E690c621eD21De08Cb559e9524Cdb8eD9"

async def investigate_wallet(wallet_address: str):
    api_key = os.getenv("MORALIS_API_KEY")
    if not api_key:
        print("[ERROR] MORALIS_API_KEY not found in .env")
        return
    
    headers = {"X-API-Key": api_key, "accept": "application/json"}
    
    print(f"[WALLET] Investigating: {wallet_address}")
    print("=" * 70)
    
    async with aiohttp.ClientSession() as session:
        # 1. Get wallet active chains
        print("\n[1] CHAIN ACTIVITY")
        print("-" * 40)
        chains_url = f"https://deep-index.moralis.io/api/v2.2/wallets/{wallet_address}/chains"
        async with session.get(chains_url, headers=headers) as resp:
            if resp.status == 200:
                chains_data = await resp.json()
                active_chains = chains_data.get("active_chains", [])
                for chain in active_chains:
                    chain_name = chain.get("chain", "unknown")
                    first_tx = chain.get("first_transaction", {})
                    last_tx = chain.get("last_transaction", {})
                    
                    first_date = first_tx.get("block_timestamp", "N/A")[:10] if first_tx else "N/A"
                    last_date = last_tx.get("block_timestamp", "N/A")[:10] if last_tx else "N/A"
                    
                    print(f"    {chain_name.upper():12} First TX: {first_date}  |  Last TX: {last_date}")
                    
                    # Calculate wallet age
                    if first_tx and first_tx.get("block_timestamp"):
                        first_dt = datetime.fromisoformat(first_tx["block_timestamp"].replace("Z", "+00:00"))
                        age_days = (datetime.now(first_dt.tzinfo) - first_dt).days
                        print(f"                    Wallet Age: {age_days} days")
            else:
                print(f"    Error fetching chains: {resp.status}")
        
        # 2. Get wallet stats on Polygon
        print("\n[2] POLYGON STATS")
        print("-" * 40)
        stats_url = f"https://deep-index.moralis.io/api/v2.2/wallets/{wallet_address}/stats?chain=polygon"
        async with session.get(stats_url, headers=headers) as resp:
            if resp.status == 200:
                stats = await resp.json()
                print(f"    NFTs Owned:    {stats.get('nfts', 'N/A')}")
                print(f"    Collections:   {stats.get('collections', 'N/A')}")
                tx_stats = stats.get("transactions", {})
                print(f"    Total TXs:     {tx_stats.get('total', 'N/A')}")
            else:
                print(f"    Error fetching stats: {resp.status}")
        
        # 3. Get USDC/USDT balances specifically 
        print("\n[3] TOKEN BALANCES")
        print("-" * 40)
        token_url = f"https://deep-index.moralis.io/api/v2.2/{wallet_address}/erc20?chain=polygon"
        async with session.get(token_url, headers=headers) as resp:
            if resp.status == 200:
                tokens = await resp.json()
                if tokens:
                    for t in tokens:
                        decimals = int(t.get("decimals", 18))
                        balance = int(t.get("balance", 0)) / (10 ** decimals)
                        symbol = t.get("symbol", "???")
                        # ASCII-safe symbol
                        symbol_safe = symbol.encode('ascii', 'replace').decode('ascii')
                        if balance > 0.01:  # Only show non-dust
                            print(f"    {symbol_safe:10} {balance:>15,.2f}")
                else:
                    print("    No ERC20 tokens found")
            else:
                print(f"    Error fetching tokens: {resp.status}")
        
        # 4. Get native balance
        print("\n[4] NATIVE BALANCE")
        print("-" * 40)
        native_url = f"https://deep-index.moralis.io/api/v2.2/{wallet_address}/balance?chain=polygon"
        async with session.get(native_url, headers=headers) as resp:
            if resp.status == 200:
                balance_data = await resp.json()
                balance_wei = int(balance_data.get("balance", 0))
                balance_matic = balance_wei / 1e18
                print(f"    MATIC:     {balance_matic:,.4f}")
            else:
                print(f"    Error: {resp.status}")
        
        # 5. Get recent transactions
        print("\n[5] RECENT TRANSACTIONS (Last 10)")
        print("-" * 40)
        tx_url = f"https://deep-index.moralis.io/api/v2.2/{wallet_address}?chain=polygon&limit=10"
        async with session.get(tx_url, headers=headers) as resp:
            if resp.status == 200:
                tx_data = await resp.json()
                txs = tx_data.get("result", [])
                for tx in txs:
                    timestamp = tx.get("block_timestamp", "")[:19]
                    value_matic = int(tx.get("value", 0)) / 1e18
                    to_addr = tx.get("to_address", "")[:20] + "..." if tx.get("to_address") else "Contract Creation"
                    method = tx.get("method_label", "transfer")
                    print(f"    {timestamp} | {value_matic:>10.4f} MATIC | {method[:15]:15} | {to_addr}")
            else:
                print(f"    Error: {resp.status}")
        
        # 6. Check for Polymarket activity (CTF contract interactions)
        print("\n[6] POLYMARKET ACTIVITY CHECK")
        print("-" * 40)
        # Polymarket CTF Exchange contract
        polymarket_contracts = [
            "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",  # CTF Exchange
            "0x4d97dcd97ec945f40cf65f87097ace5ea0476045",  # Neg Risk CTF
        ]
        
        tx_url = f"https://deep-index.moralis.io/api/v2.2/{wallet_address}?chain=polygon&limit=100"
        async with session.get(tx_url, headers=headers) as resp:
            if resp.status == 200:
                all_txs = (await resp.json()).get("result", [])
                poly_txs = [tx for tx in all_txs if tx.get("to_address", "").lower() in polymarket_contracts]
                print(f"    Polymarket TXs: {len(poly_txs)}")
                
                if poly_txs:
                    print(f"    First Polymarket TX: {poly_txs[-1].get('block_timestamp', 'N/A')[:10]}")
                    print(f"    Last Polymarket TX:  {poly_txs[0].get('block_timestamp', 'N/A')[:10]}")
            else:
                print(f"    Error: {resp.status}")
        
        print("\n" + "=" * 70)
        print("[ANALYSIS COMPLETE]")

if __name__ == "__main__":
    asyncio.run(investigate_wallet(WALLET_ADDRESS))
