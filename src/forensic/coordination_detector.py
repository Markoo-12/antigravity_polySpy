"""
Coordination Detector - Database-backed Sybil/Cluster Detection.

Queries trades.db for temporal synchronization patterns to detect
coordinated trading by multiple wallets.

This module is specifically designed for historical/database analysis,
complementing the real-time cluster_detector.py module.
"""
import aiosqlite
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from ..config import (
    DATABASE_PATH,
    CLUSTER_WINDOW_MINUTES,
    CLUSTER_THRESHOLD_WALLETS,
)


# Configuration
COORDINATION_WINDOW_MINUTES = 30  # Time window for clustering (Shadow-Whale: 30 min)
COORDINATION_FACTOR = 1.5  # Multiplier when Sybil cluster detected


@dataclass
class CoordinationResult:
    """Result of coordination/Sybil detection analysis."""
    is_coordinated: bool = False
    factor: float = 1.0  # 1.0 = no cluster, 1.5 = cluster detected
    cluster_wallets: List[str] = field(default_factory=list)
    cluster_size: int = 0
    time_window_minutes: int = COORDINATION_WINDOW_MINUTES
    total_cluster_amount_usdc: float = 0.0
    analysis_note: str = ""


async def detect_coordination(
    asset_id: str,
    timestamp: datetime,
    window_minutes: int = COORDINATION_WINDOW_MINUTES,
    min_wallets: int = CLUSTER_THRESHOLD_WALLETS,
    db_path: str = DATABASE_PATH,
) -> CoordinationResult:
    """
    Query trades.db for temporal synchronization patterns.
    
    Logic:
    1. Query trades within ±window_minutes of the given timestamp for the same asset_id
    2. Count unique owner_address entries
    3. If ≥min_wallets unique addresses exist, flag as "Cluster"
    
    Args:
        asset_id: The outcome token asset ID
        timestamp: Reference timestamp to check around
        window_minutes: Time window size (default 10 minutes)
        min_wallets: Minimum wallets to trigger cluster (default 3)
        db_path: Path to trades.db
        
    Returns:
        CoordinationResult with is_coordinated flag and factor
    """
    result = CoordinationResult(time_window_minutes=window_minutes)
    
    # Ensure timestamp is timezone-aware
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    
    # Calculate time window bounds
    window_start = timestamp - timedelta(minutes=window_minutes)
    window_end = timestamp + timedelta(minutes=window_minutes)
    
    try:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            
            # Query for trades in the same asset within the time window
            cursor = await db.execute(
                """
                SELECT DISTINCT owner_address, amount_usdc, timestamp
                FROM trades
                WHERE asset_id = ?
                  AND timestamp >= ?
                  AND timestamp <= ?
                  AND owner_address IS NOT NULL
                ORDER BY timestamp ASC
                """,
                (
                    asset_id,
                    window_start.isoformat(),
                    window_end.isoformat(),
                ),
            )
            
            rows = await cursor.fetchall()
            
            if not rows:
                result.analysis_note = "No trades found in window"
                return result
            
            # Get unique wallets and total amount
            unique_wallets = set()
            total_amount = 0.0
            
            for row in rows:
                owner = row["owner_address"]
                if owner:
                    unique_wallets.add(owner)
                    total_amount += row["amount_usdc"]
            
            cluster_size = len(unique_wallets)
            result.cluster_size = cluster_size
            result.cluster_wallets = list(unique_wallets)
            result.total_cluster_amount_usdc = total_amount
            
            # Check if cluster threshold is met
            if cluster_size >= min_wallets:
                result.is_coordinated = True
                result.factor = COORDINATION_FACTOR
                result.analysis_note = (
                    f"Sybil cluster detected: {cluster_size} unique wallets "
                    f"within {window_minutes * 2} minutes, "
                    f"total ${total_amount:,.0f}"
                )
                print(f"[COORD] {result.analysis_note}")
            else:
                result.analysis_note = (
                    f"{cluster_size} wallets in window (threshold: {min_wallets})"
                )
    
    except Exception as e:
        result.analysis_note = f"Coordination check error: {str(e)[:50]}"
        print(f"[COORD] Error: {e}")
    
    return result


async def get_coordination_clusters(
    lookback_hours: int = 24,
    min_wallets: int = CLUSTER_THRESHOLD_WALLETS,
    db_path: str = DATABASE_PATH,
) -> List[dict]:
    """
    Find all coordination clusters in the database within a lookback period.
    
    Useful for batch analysis and reporting.
    
    Args:
        lookback_hours: How far back to look for clusters
        min_wallets: Minimum wallets per cluster
        db_path: Path to trades.db
        
    Returns:
        List of cluster dictionaries with asset_id, wallets, and amounts
    """
    clusters = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    
    try:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            
            # Get distinct asset IDs with multiple trades in the period
            cursor = await db.execute(
                """
                SELECT asset_id, MIN(timestamp) as first_trade, MAX(timestamp) as last_trade,
                       COUNT(DISTINCT owner_address) as wallet_count,
                       SUM(amount_usdc) as total_amount
                FROM trades
                WHERE timestamp >= ?
                  AND owner_address IS NOT NULL
                GROUP BY asset_id
                HAVING wallet_count >= ?
                ORDER BY wallet_count DESC
                """,
                (cutoff.isoformat(), min_wallets),
            )
            
            rows = await cursor.fetchall()
            
            for row in rows:
                # For each potential cluster, check time window
                asset_id = row["asset_id"]
                first_trade = datetime.fromisoformat(row["first_trade"])
                last_trade = datetime.fromisoformat(row["last_trade"])
                
                # Only flag if trades are within coordination window
                time_span = (last_trade - first_trade).total_seconds() / 60
                if time_span <= COORDINATION_WINDOW_MINUTES * 2:
                    clusters.append({
                        "asset_id": asset_id,
                        "wallet_count": row["wallet_count"],
                        "total_amount_usdc": row["total_amount"],
                        "time_span_minutes": time_span,
                        "first_trade": first_trade.isoformat(),
                        "last_trade": last_trade.isoformat(),
                    })
    
    except Exception as e:
        print(f"[COORD] Cluster scan error: {e}")
    
    return clusters
