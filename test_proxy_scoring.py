"""Test the refactored scorer with proxy fallback."""
import asyncio
import sqlite3
from datetime import datetime

from src.forensic.scorer import InsiderScorer


async def main():
    print("=" * 70)
    print("TESTING SCORER WITH PROXY FALLBACK")
    print("=" * 70)
    
    # Get a whale trade that previously had owner=None
    conn = sqlite3.connect(r'data/trades.db')
    c = conn.cursor()
    c.execute('''
        SELECT proxy_address, amount_usdc, asset_id, timestamp, owner_address
        FROM trades 
        WHERE owner_address IS NULL AND amount_usdc > 100000
        ORDER BY amount_usdc DESC
        LIMIT 3
    ''')
    
    trades = c.fetchall()
    conn.close()
    
    if not trades:
        print("No trades with owner=None found")
        return
    
    # Create scorer
    scorer = InsiderScorer()
    
    for proxy, amount, asset_id, ts_str, owner in trades:
        print(f"\n{'='*60}")
        print(f"TRADE: ${amount:,.0f} USDC")
        print(f"Proxy: {proxy}")
        print(f"Owner: {owner or 'NONE (will use proxy)'}")
        print("-" * 60)
        
        # Parse timestamp
        ts = datetime.fromisoformat(ts_str)
        
        # Calculate score with proxy fallback
        result = await scorer.calculate_score(
            owner_address=owner,  # This is None
            trade_timestamp=ts,
            trade_amount_usdc=amount,
            asset_id=asset_id,
            proxy_address=proxy,  # NEW: fallback
        )
        
        print(f"\nSCORE: {result.score}/100+")
        print(f"Base Score: {result.base_score}")
        print(f"Coordination Factor: {result.coordination_factor}")
        print(f"\nReasons:")
        for r in result.reasons:
            print(f"  - {r}")
        
        print(f"\nFeature Breakdown:")
        for feat, pts in result.feature_scores.items():
            print(f"  {feat}: +{pts}")
    
    print("\n" + "=" * 70)
    print("TEST COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
