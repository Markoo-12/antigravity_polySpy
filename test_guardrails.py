"""
Test script for Anti-Bot Guardrails verification.
"""
import asyncio
import os
import aiosqlite
from datetime import datetime, timedelta
from src.database.repository import TradeRepository, Trade
from src.database.blacklist_repo import BlacklistRepository
from src.forensic.guardrails import GuardrailFilter

TEST_DB = "test_guardrails.db"

async def setup_test_db():
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    
    # Initialize tables manually for test
    async with aiosqlite.connect(TEST_DB) as db:
        await db.execute("""
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tx_hash TEXT, block_number INTEGER, timestamp TIMESTAMP,
                order_hash TEXT, proxy_address TEXT, owner_address TEXT,
                proxy_type TEXT, asset_id TEXT, side TEXT, amount_usdc REAL, market_id TEXT,
                insider_score INTEGER, score_reasons TEXT, bridge_funded BOOLEAN, 
                bridge_name TEXT, win_rate REAL, analyzed_at TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE blacklist (
                address TEXT PRIMARY KEY,
                type TEXT,
                count INTEGER DEFAULT 1,
                is_blocked BOOLEAN DEFAULT 0,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()

async def run_tests():
    print("[TEST] Setting up test environment...")
    await setup_test_db()
    
    trade_repo = TradeRepository(TEST_DB)
    blacklist_repo = BlacklistRepository(TEST_DB)
    guardrail = GuardrailFilter(trade_repo, blacklist_repo)
    
    wallet_A = "0xWalletA_Symmetric"
    wallet_B = "0xWalletB_Dumper"
    wallet_C = "0xWalletC_Wash"
    asset_1 = "0xToken1"
    
    # --- TEST 1: SYMMETRIC TRADER ---
    print("\n[TEST 1] Symmetric Trader (Rebalancing Bot)")
    # Insert a BUY 5 mins ago
    await trade_repo.insert_trade(Trade(
        tx_hash="0x1", block_number=1, timestamp=datetime.utcnow() - timedelta(minutes=5),
        order_hash="0x1", proxy_address=wallet_A, owner_address=None, proxy_type="",
        asset_id=asset_1, side="buy", amount_usdc=5000.0
    ))
    
    # Simulate a SELL of the same amount now
    sell_trade = Trade(
        tx_hash="0x2", block_number=2, timestamp=datetime.utcnow(),
        order_hash="0x2", proxy_address=wallet_A, owner_address=None, proxy_type="",
        asset_id=asset_1, side="sell", amount_usdc=5000.0 
    )
    
    result = await guardrail.check_all(sell_trade, wallet_A)
    if result.should_discard and "Symmetric" in result.reason:
        print("[PASS] Symmetric trade detected and discard recommended.")
    else:
        print(f"[FAIL] Expected symmetric detect. Got: {result}")


    # --- TEST 2: NET POSITION DUMP ---
    print("\n[TEST 2] Net Position Dump (>90% in 15m)")
    # Insert a large BUY $20k 10 mins ago
    await trade_repo.insert_trade(Trade(
        tx_hash="0x3", block_number=3, timestamp=datetime.utcnow() - timedelta(minutes=10),
        order_hash="0x3", proxy_address=wallet_B, owner_address=None, proxy_type="",
        asset_id=asset_1, side="buy", amount_usdc=20000.0
    ))
    
    # Simulate a SELL of $19k (95%)
    dump_trade = Trade(
        tx_hash="0x4", block_number=4, timestamp=datetime.utcnow(),
        order_hash="0x4", proxy_address=wallet_B, owner_address=None, proxy_type="",
        asset_id=asset_1, side="sell", amount_usdc=19000.0 
    )
    
    result = await guardrail.check_all(dump_trade, wallet_B)
    if result.should_discard and "Dump" in result.reason and result.should_blacklist:
        print("[PASS] Dump detected, discard + blacklist recommended.")
        # Apply flag
        await blacklist_repo.flag_address(wallet_B, result.blacklist_type)
    else:
        print(f"[FAIL] Expected dump detect. Got: {result}")


    # --- TEST 3: WASH TRADER ---
    print("\n[TEST 3] Wash Trader (Volume/Hold Ratio)")
    # Insert huge volume $1.5M but small trades
    for i in range(15):
        await trade_repo.insert_trade(Trade(
            tx_hash=f"0xWash{i}", block_number=100+i, timestamp=datetime.utcnow(),
            order_hash="0x...", proxy_address=wallet_C, owner_address=None, proxy_type="",
            asset_id=asset_1, side="buy", amount_usdc=100000.0 # $100k
        ))
    # BUT Max single position (measured by max BUY in our simplified query) is just $100k?
    # Wait, our logic check was Lifetime Volume vs Max Position.
    # $1.5M vol, max pos $100k = Ratio 15. That is < 200.
    # To trigger > 200, we need $1M vol and MAX POS < $5k.
    
    # Let's retry setup for Wallet D (Real Washer)
    wallet_D = "0xWalletD_RealWash"
    print("\n[TEST 3b] Real Wash Trader Scenarion")
    # 250 trades of $4000 = $1,000,000 Volume. Max Pos = $4000. Ratio = 250.
    for i in range(250):
         await trade_repo.insert_trade(Trade(
            tx_hash=f"0xW{i}", block_number=1000+i, timestamp=datetime.utcnow(),
            order_hash="0x...", proxy_address=wallet_D, owner_address=None, proxy_type="",
            asset_id=asset_1, side="buy", amount_usdc=4000.0
        ))
    
    # Trigger check on next trade
    check_trade = Trade(
        tx_hash="0xFinal", block_number=2000, timestamp=datetime.utcnow(),
        order_hash="0x...", proxy_address=wallet_D, owner_address=None, proxy_type="",
        asset_id=asset_1, side="buy", amount_usdc=4000.0 
    )
    
    result = await guardrail.check_all(check_trade, wallet_D)
    if result.should_discard and "Wash" in result.reason:
        print("[PASS] Wash trader detected.")
    else:
        print(f"[FAIL] Expected wash detect. Got: {result}")


    # --- TEST 4: BLACKLIST COUNTING ---
    print("\n[TEST 4] Blacklist Enforcement")
    # Wallet B was flagged once in Test 2. Let's flag it 3 more times.
    await blacklist_repo.flag_address(wallet_B, "bot") # Flag 2
    await blacklist_repo.flag_address(wallet_B, "bot") # Flag 3
    is_blocked = await blacklist_repo.flag_address(wallet_B, "bot") # Flag 4 -> SHOULD BLOCK
    
    if is_blocked:
        print("[PASS] Wallet B is now permanently blocked after 4th flag.")
    else:
        print("[FAIL] Wallet B should be blocked.")
        
    # Verify DB state
    if await blacklist_repo.is_blocked(wallet_B):
        print("[PASS] DB correctly reports is_blocked=True")
    
    # Clean up
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)

if __name__ == "__main__":
    asyncio.run(run_tests())
