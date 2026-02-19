"""
Script to re-score trades using the NEW forensic system.
Tests: Slippage Insensitivity, Low-Probability Trigger, Drip Detection.
"""
import asyncio
import sqlite3
from datetime import datetime, timezone

from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider

from src.config import POLYGON_HTTP_URL, FORENSIC_USDC_THRESHOLD, DATABASE_PATH
from src.demasker import AddressResolver
from src.forensic.scorer import InsiderScorer
from src.database.repository import TradeRepository
from src.execution import UpsideValidator


async def main():
    print("=" * 70)
    print("RE-SCORING TRADES WITH NEW HEURISTICS")
    print("  - Slippage Insensitivity (+25)")
    print("  - Low-Probability Trigger (+40)")
    print("  - Drip Detection (+35)")
    print("=" * 70)
    
    # 1. Fetch trades directly from DB
    print(f"\nFetching 50 recent trades above ${FORENSIC_USDC_THRESHOLD:,.0f}...")
    
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    c.execute('''
        SELECT * FROM trades 
        WHERE amount_usdc >= ?
        ORDER BY timestamp DESC
        LIMIT 50
    ''', (FORENSIC_USDC_THRESHOLD,))
    
    rows = c.fetchall()
    conn.close()
    
    print(f"Found {len(rows)} trades to re-score.\n")
    
    # 2. Initialize components
    w3 = AsyncWeb3(AsyncHTTPProvider(POLYGON_HTTP_URL))
    resolver = AddressResolver(w3)
    repository = TradeRepository(DATABASE_PATH)
    scorer = InsiderScorer(repository=repository)  # Pass repository for Drip Detection
    upside_validator = UpsideValidator()
    
    rescored_count = 0
    high_score_count = 0
    feature_triggers = {
        "slippage_insensitivity": 0,
        "low_probability_trigger": 0,
        "quiet_accumulation": 0,
    }
    
    for row in rows:
        trade_id = row["id"]
        amount = row["amount_usdc"]
        proxy = row["proxy_address"]
        existing_owner = row["owner_address"]
        ts_str = row["timestamp"]
        asset_id = row["asset_id"]
        trade_price = row["price"] if "price" in row.keys() else None
        
        print("-" * 70)
        print(f"Trade #{trade_id} | ${amount:,.0f} | Price: {trade_price:.4f}" if trade_price else f"Trade #{trade_id} | ${amount:,.0f} | Price: N/A")
        
        try:
            # 3. Resolve Owner
            owner, proxy_type = await resolver.resolve(proxy)
            owner = owner or proxy
            
            # 4. Get current mid-price and slippage
            current_price = None
            slippage_percent = 0.0
            
            try:
                upside_result = await upside_validator.validate(
                    asset_id=asset_id,
                    insider_entry_price=None,
                    side="buy",
                )
                current_price = upside_result.current_price
                slippage_percent = upside_result.slippage_percent or 0.0
            except Exception:
                pass  # Order book fetch failed
            
            # 5. Calculate Score with ALL new parameters
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            
            score_result = await scorer.calculate_score(
                owner_address=owner,
                trade_timestamp=ts,
                trade_amount_usdc=amount,
                asset_id=asset_id,
                price=trade_price,
                slippage_percent=slippage_percent,
                current_mid_price=current_price,
                proxy_address=proxy,
            )
            
            # 6. Update Database
            bridge_funded = score_result.bridge_result.is_bridge_funded if score_result.bridge_result else False
            bridge_name = score_result.bridge_result.bridge_name if score_result.bridge_result else None
            win_rate = score_result.win_rate_result.win_rate if score_result.win_rate_result else None
            
            await repository.update_insider_score(
                trade_id=trade_id,
                insider_score=score_result.score,
                score_reasons=score_result.to_json(),
                bridge_funded=bridge_funded,
                bridge_name=bridge_name,
                win_rate=win_rate,
            )
            
            # 7. Display Results
            icon = "[ALERT]" if score_result.is_alert_worthy else "[SCORE]"
            print(f"  {icon} Score: {score_result.score}/100+")
            
            for reason in score_result.reasons:
                print(f"    - {reason}")
            
            # Track feature triggers
            for feature in feature_triggers:
                if feature in score_result.feature_scores:
                    feature_triggers[feature] += 1
            
            rescored_count += 1
            if score_result.score >= 75:
                high_score_count += 1
                
        except Exception as e:
            print(f"  [ERROR] {e}")
            continue

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Trades Rescored: {rescored_count}")
    print(f"High Score (>=75): {high_score_count}")
    print(f"\nNew Feature Triggers:")
    print(f"  Slippage Insensitivity: {feature_triggers['slippage_insensitivity']}")
    print(f"  Low-Probability Trigger: {feature_triggers['low_probability_trigger']}")
    print(f"  Drip Detection (Quiet Acc): {feature_triggers['quiet_accumulation']}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())

