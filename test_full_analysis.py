"""
Test PolygonScan resolver on trades that had owner=None.
Then run full wallet analysis to get age, win rate, activity.
"""
import asyncio
import sqlite3
from datetime import datetime

from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider

from src.config import POLYGON_HTTP_URL
from src.demasker import AddressResolver
from src.forensic.scorer import InsiderScorer


async def main():
    print("=" * 70)
    print("TESTING POLYGONSCAN RESOLVER + FULL WALLET ANALYSIS")
    print("=" * 70)
    
    # Get whale trades with owner=None
    conn = sqlite3.connect(r'data/trades.db')
    c = conn.cursor()
    c.execute('''
        SELECT id, proxy_address, amount_usdc, asset_id, timestamp
        FROM trades 
        WHERE owner_address IS NULL AND amount_usdc > 100000
        ORDER BY amount_usdc DESC
        LIMIT 5
    ''')
    
    trades = c.fetchall()
    conn.close()
    
    if not trades:
        print("No trades with owner=None found!")
        return
    
    print(f"\nFound {len(trades)} whale trades with owner=None")
    print("-" * 70)
    
    # Create resolver and scorer
    w3 = AsyncWeb3(AsyncHTTPProvider(POLYGON_HTTP_URL))
    resolver = AddressResolver(w3)
    scorer = InsiderScorer()
    
    resolved_count = 0
    
    for trade_id, proxy, amount, asset_id, ts_str in trades:
        print(f"\n{'='*60}")
        print(f"TRADE #{trade_id}: ${amount:,.0f} USDC")
        print(f"Proxy: {proxy}")
        print("-" * 60)
        
        # Step 1: Try to resolve owner using PolygonScan
        print("\n[STEP 1] Resolving owner address...")
        owner, proxy_type = await resolver.resolve(proxy)
        
        if owner:
            resolved_count += 1
            print(f"SUCCESS! Owner found: {owner}")
            print(f"Proxy Type: {proxy_type.value}")
            
            # Step 2: Run full scoring with resolved owner
            print("\n[STEP 2] Running full forensic analysis...")
            ts = datetime.fromisoformat(ts_str)
            
            result = await scorer.calculate_score(
                owner_address=owner,
                trade_timestamp=ts,
                trade_amount_usdc=amount,
                asset_id=asset_id,
                proxy_address=proxy,
            )
            
            print(f"\n*** INSIDER SCORE: {result.score}/100+ ***")
            print(f"Base Score: {result.base_score}")
            print(f"Coordination Factor: {result.coordination_factor}")
            
            print(f"\nReasons:")
            for r in result.reasons:
                print(f"  - {r}")
            
            print(f"\nFeature Breakdown:")
            for feat, pts in result.feature_scores.items():
                print(f"  {feat}: +{pts}")
            
            # Show wallet analysis details
            if result.wallet_result:
                wr = result.wallet_result
                print(f"\n[WALLET ANALYSIS]")
                print(f"  Wallet Age: {wr.wallet_age_days:.1f} days" if wr.wallet_age_days else "  Wallet Age: Unknown")
                print(f"  Total Transactions: {wr.total_transactions}")
                print(f"  Is New Wallet (<72h): {wr.is_new_wallet}")
                print(f"  Is Low Activity (<20 txns): {wr.is_low_activity}")
            
            if result.win_rate_result:
                wrr = result.win_rate_result
                print(f"\n[WIN RATE ANALYSIS]")
                print(f"  Win Rate: {wrr.win_rate*100:.1f}%" if wrr.win_rate else "  Win Rate: Unknown")
                print(f"  Total Positions: {wrr.total_positions}")
                print(f"  Total Volume: ${wrr.total_volume_usdc:,.0f}")
        
        else:
            print(f"FAILED to resolve owner")
            print(f"Proxy Type: {proxy_type.value}")
    
    print("\n" + "=" * 70)
    print(f"SUMMARY: Resolved {resolved_count}/{len(trades)} trades")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
