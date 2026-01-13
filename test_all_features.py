"""
Comprehensive test of all wallet analysis features.
Tests: wallet age, low activity, binary concentration, win rate
"""
import asyncio
import sqlite3
from datetime import datetime, timezone

from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider

from src.config import POLYGON_HTTP_URL
from src.demasker import AddressResolver
from src.forensic.wallet_analyzer import WalletAnalyzer
from src.forensic.win_rate_analyzer import WinRateAnalyzer
from src.forensic.bridge_detector import BridgeDetector


async def main():
    print("=" * 70)
    print("COMPREHENSIVE WALLET ANALYSIS TEST")
    print("=" * 70)
    
    # Get diverse trades from database
    conn = sqlite3.connect(r'data/trades.db')
    c = conn.cursor()
    
    # Get a variety of trades: some with owners, some without
    c.execute('''
        SELECT id, proxy_address, owner_address, amount_usdc, asset_id, timestamp
        FROM trades 
        WHERE amount_usdc > 10000
        ORDER BY RANDOM()
        LIMIT 10
    ''')
    
    trades = c.fetchall()
    conn.close()
    
    print(f"\nTesting {len(trades)} random trades above $10k threshold")
    print("-" * 70)
    
    # Initialize components
    w3 = AsyncWeb3(AsyncHTTPProvider(POLYGON_HTTP_URL))
    resolver = AddressResolver(w3)
    wallet_analyzer = WalletAnalyzer()
    win_rate_analyzer = WinRateAnalyzer()
    bridge_detector = BridgeDetector()
    
    for trade_id, proxy, owner_db, amount, asset_id, ts_str in trades:
        print(f"\n{'='*60}")
        print(f"TRADE #{trade_id}: ${amount:,.0f} USDC")
        print(f"Proxy: {proxy[:30]}...")
        print(f"DB Owner: {owner_db[:30] + '...' if owner_db else 'None'}")
        print("-" * 60)
        
        # Get the owner (resolve if needed)
        if owner_db:
            owner = owner_db
            print(f"[OWNER] Using DB owner: {owner[:30]}...")
        else:
            print(f"[OWNER] Resolving via PolygonScan...")
            owner, ptype = await resolver.resolve(proxy)
            if owner:
                print(f"[OWNER] Resolved: {owner[:30]}...")
            else:
                print(f"[OWNER] Using proxy as fallback")
                owner = proxy
        
        ts = datetime.fromisoformat(ts_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        
        # Run all analysis methods
        print(f"\n[ANALYSIS] Running all checks on {owner[:20]}...")
        
        # 1. Wallet Age & Activity
        wallet_result = await wallet_analyzer.analyze_wallet(
            wallet_address=owner,
            trade_timestamp=ts,
            current_asset_id=asset_id,
            trade_amount_usdc=amount,
        )
        
        print(f"\n[WALLET ANALYSIS]")
        if wallet_result.wallet_age_days:
            print(f"  Wallet Age: {wallet_result.wallet_age_days:.1f} days")
        else:
            print(f"  Wallet Age: Unknown")
        print(f"  Total Transactions: {wallet_result.total_transactions:,}")
        print(f"  Is New Wallet (<72h): {wallet_result.is_new_wallet} {'[+50 pts]' if wallet_result.is_new_wallet else ''}")
        print(f"  Is Low Activity (<20 txns): {wallet_result.is_low_activity} {'[+20 pts]' if wallet_result.is_low_activity else ''}")
        print(f"  Is Single Market: {wallet_result.is_single_market} {'[+30 pts]' if wallet_result.is_single_market else ''}")
        print(f"  Binary Concentration: {wallet_result.is_binary_concentration} {'[+25 pts]' if wallet_result.is_binary_concentration else ''}")
        print(f"  Round Number: {wallet_result.is_round_number} {'[+15 pts]' if wallet_result.is_round_number else ''}")
        print(f"  Wallet Score: +{wallet_result.score_points} pts")
        
        # 2. Win Rate Analysis (use PROXY address - that's where ERC-1155 tokens are)
        win_result = await win_rate_analyzer.calculate_win_rate(proxy)
        
        print(f"\n[WIN RATE ANALYSIS]")
        if win_result.win_rate is not None:
            print(f"  Win Rate: {win_result.win_rate*100:.1f}%")
            print(f"  Total Positions: {win_result.total_positions}")
            print(f"  Winning Positions: {win_result.winning_positions}")
            print(f"  Total Volume: ${win_result.total_volume_usdc:,.0f}")
            print(f"  Win Rate Score: +{win_result.score_points} pts {'[HIGH WIN RATE]' if win_result.score_points > 0 else ''}")
        else:
            print(f"  Win Rate: {win_result.analysis_note}")
        
        # 3. Bridge Detection
        bridge_result = await bridge_detector.check_bridge_funding(owner, ts)
        
        print(f"\n[BRIDGE DETECTION]")
        if bridge_result.is_bridge_funded:
            print(f"  Bridge Funded: YES via {bridge_result.bridge_name}")
            print(f"  Hours Before Trade: {bridge_result.hours_before_trade}")
            print(f"  Bridge Score: +{bridge_result.score_points} pts")
        else:
            print(f"  Bridge Funded: No")
        
        # Summary
        total_score = wallet_result.score_points + win_result.score_points + bridge_result.score_points
        print(f"\n[TOTAL FROM WALLET ANALYSIS: +{total_score} pts]")
    
    print("\n" + "=" * 70)
    print("TEST COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
