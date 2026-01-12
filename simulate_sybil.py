"""
Sybil Cluster Simulation Script
Scans trades.db for coordinated trading patterns and outputs top clusters.
"""
import asyncio
from src.forensic.coordination_detector import get_coordination_clusters
from src.config import DATABASE_PATH


async def main():
    print("=" * 60)
    print("SYBIL CLUSTER SIMULATION - trades.db")
    print("=" * 60)
    print(f"Database: {DATABASE_PATH}")
    print()
    
    # Scan for clusters in last 7 days (168 hours)
    clusters = await get_coordination_clusters(lookback_hours=168, min_wallets=2)
    
    if not clusters:
        print("No coordination clusters found in database.")
        print("(This could mean trades.db is empty or no clusters exist)")
        return
    
    # Sort by wallet count descending
    clusters.sort(key=lambda x: x["wallet_count"], reverse=True)
    
    print(f"Found {len(clusters)} potential clusters")
    print()
    
    # Show top 3
    for i, cluster in enumerate(clusters[:3], 1):
        asset_id = cluster["asset_id"]
        display_id = asset_id[:20] + "..." if len(asset_id) > 20 else asset_id
        
        print(f"--- CLUSTER #{i} ---")
        print(f"  Asset ID: {display_id}")
        print(f"  Wallets: {cluster['wallet_count']}")
        print(f"  Total Amount: ${cluster['total_amount_usdc']:,.0f}")
        print(f"  Time Span: {cluster['time_span_minutes']:.1f} min")
        print(f"  First Trade: {cluster['first_trade']}")
        print(f"  Last Trade: {cluster['last_trade']}")
        print()
    
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
