"""
Script to re-score 100 suspicious transactions using the new forensic system.
Fetches recent trades > $10k, resolves owners (with factory detection),
runs full forensic analysis (wallet age, activity, win rate, bridge),
and updates the database with new scores.
"""
import asyncio
import sqlite3
import json
from datetime import datetime, timezone

from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider

from src.config import POLYGON_HTTP_URL, FORENSIC_USDC_THRESHOLD, DATABASE_PATH
from src.demasker import AddressResolver
from src.forensic.scorer import InsiderScorer
from src.database.repository import TradeRepository


async def main():
    print("=" * 70)
    print("RE-SCORING SUSPICIOUS TRANSACTIONS")
    print("=" * 70)
    
    # 1. Fetch trades directly from DB
    print(f"Fetching 100 recent trades above ${FORENSIC_USDC_THRESHOLD:,.0f}...")
    
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # Get 100 recent trades above threshold
    c.execute('''
        SELECT * FROM trades 
        WHERE amount_usdc >= ?
        ORDER BY timestamp DESC
        LIMIT 100
    ''', (FORENSIC_USDC_THRESHOLD,))
    
    rows = c.fetchall()
    conn.close()
    
    print(f"Found {len(rows)} trades to re-score.")
    print("-" * 70)
    
    # 2. Initialize forensic components
    w3 = AsyncWeb3(AsyncHTTPProvider(POLYGON_HTTP_URL))
    resolver = AddressResolver(w3)
    scorer = InsiderScorer()
    repository = TradeRepository(DATABASE_PATH)
    
    rescored_count = 0
    high_score_count = 0
    
    for row in rows:
        trade_id = row["id"]
        amount = row["amount_usdc"]
        proxy = row["proxy_address"]
        existing_owner = row["owner_address"]
        ts_str = row["timestamp"]
        asset_id = row["asset_id"]
        
        print(f"\nProcessing Trade #{trade_id} (${amount:,.0f})")
        
        try:
            # 3. Resolve Owner (using improved logic)
            # We always re-resolve to catch factories that might have been missed
            owner, proxy_type = await resolver.resolve(proxy)
            
            if owner:
                print(f"  Owner Resolved: {owner[:10]}... ({proxy_type.name})")
                # Update owner in DB if changed
                if owner != existing_owner:
                    await repository.update_owner(trade_id, owner, proxy_type.name)
            else:
                print(f"  Owner: Unknown (using proxy)")
                
            # 4. Calculate Score
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            
            score_result = await scorer.calculate_score(
                owner_address=owner,
                trade_timestamp=ts,
                trade_amount_usdc=amount,
                asset_id=asset_id,
                proxy_address=proxy,
            )
            
            # 5. Update Database
            bridge_funded = False
            bridge_name = None
            win_rate = None
            
            if score_result.bridge_result:
                bridge_funded = score_result.bridge_result.is_bridge_funded
                bridge_name = score_result.bridge_result.bridge_name
            
            if score_result.win_rate_result:
                win_rate = score_result.win_rate_result.win_rate
            
            await repository.update_insider_score(
                trade_id=trade_id,
                insider_score=score_result.score,
                score_reasons=score_result.to_json(),
                bridge_funded=bridge_funded,
                bridge_name=bridge_name,
                win_rate=win_rate,
            )
            
            print(f"  Score: {score_result.score}/100")
            if score_result.reasons:
                print(f"  Reasons: {score_result.reasons}")
            
            rescored_count += 1
            if score_result.score >= 70:
                high_score_count += 1
                
        except Exception as e:
            print(f"  [ERROR] Failed to score trade #{trade_id}: {e}")
            continue

    print("\n" + "=" * 70)
    print(f"RE-SCORING COMPLETE")
    print(f"Total Processed: {rescored_count}")
    print(f"High Score Trades (>=70): {high_score_count}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
