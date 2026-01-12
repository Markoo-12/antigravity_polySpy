"""
Execution Cluster Detector - Immediate alert for rapid coordinated trading.

Detects when 3+ wallets buy the same asset within 120 seconds.
This triggers an IMMEDIATE alert regardless of individual score.

Part of the Shadow-Whale Forensic Sentinel.
"""
import aiosqlite
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from ..config import DATABASE_PATH


# Configuration
EXECUTION_CLUSTER_WINDOW_SECONDS = 120  # 2 minutes
EXECUTION_CLUSTER_MIN_WALLETS = 3  # Minimum wallets for immediate alert


@dataclass
class ExecutionClusterAlert:
    """Alert when 3+ wallets trade same asset in 120 seconds."""
    asset_id: str
    wallets: List[str] = field(default_factory=list)
    total_amount_usdc: float = 0.0
    time_span_seconds: int = 0
    first_trade: Optional[datetime] = None
    last_trade: Optional[datetime] = None
    is_immediate_alert: bool = True  # Always bypass score threshold
    tx_hashes: List[str] = field(default_factory=list)


async def detect_execution_cluster(
    asset_id: str,
    timestamp: datetime,
    window_seconds: int = EXECUTION_CLUSTER_WINDOW_SECONDS,
    min_wallets: int = EXECUTION_CLUSTER_MIN_WALLETS,
    db_path: str = DATABASE_PATH,
) -> Optional[ExecutionClusterAlert]:
    """
    Detect rapid coordinated trading (3+ wallets in 120 seconds).
    
    This is a "Coordinated Strike" that triggers immediate alert
    regardless of individual insider scores.
    
    Args:
        asset_id: The outcome token asset ID
        timestamp: Reference timestamp to check around
        window_seconds: Time window (default 120 seconds)
        min_wallets: Minimum wallets for alert (default 3)
        db_path: Path to trades.db
        
    Returns:
        ExecutionClusterAlert if cluster detected, None otherwise
    """
    # Ensure timestamp is timezone-aware
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    
    # Calculate tight time window
    window_start = timestamp - timedelta(seconds=window_seconds)
    window_end = timestamp + timedelta(seconds=window_seconds)
    
    try:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            
            # Query for trades in the same asset within the tight window
            cursor = await db.execute(
                """
                SELECT owner_address, amount_usdc, timestamp, tx_hash
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
            
            if len(rows) < min_wallets:
                return None
            
            # Get unique wallets and their trades
            wallet_trades = {}
            tx_hashes = []
            total_amount = 0.0
            first_ts = None
            last_ts = None
            
            for row in rows:
                owner = row["owner_address"]
                if owner and owner not in wallet_trades:
                    wallet_trades[owner] = {
                        "amount": row["amount_usdc"],
                        "timestamp": row["timestamp"],
                    }
                    tx_hashes.append(row["tx_hash"])
                    total_amount += row["amount_usdc"]
                    
                    ts = datetime.fromisoformat(row["timestamp"])
                    if first_ts is None or ts < first_ts:
                        first_ts = ts
                    if last_ts is None or ts > last_ts:
                        last_ts = ts
            
            # Check if we have enough unique wallets
            if len(wallet_trades) >= min_wallets:
                time_span = int((last_ts - first_ts).total_seconds()) if first_ts and last_ts else 0
                
                alert = ExecutionClusterAlert(
                    asset_id=asset_id,
                    wallets=list(wallet_trades.keys()),
                    total_amount_usdc=total_amount,
                    time_span_seconds=time_span,
                    first_trade=first_ts,
                    last_trade=last_ts,
                    tx_hashes=tx_hashes,
                )
                
                print(f"[EXEC_CLUSTER] COORDINATED STRIKE: {len(wallet_trades)} wallets in {time_span}s, ${total_amount:,.0f}")
                return alert
            
            return None
    
    except Exception as e:
        print(f"[EXEC_CLUSTER] Detection error: {e}")
        return None


async def scan_recent_execution_clusters(
    lookback_minutes: int = 30,
    window_seconds: int = EXECUTION_CLUSTER_WINDOW_SECONDS,
    min_wallets: int = EXECUTION_CLUSTER_MIN_WALLETS,
    db_path: str = DATABASE_PATH,
) -> List[ExecutionClusterAlert]:
    """
    Scan recent trades for execution clusters.
    
    Useful for batch detection and monitoring.
    
    Args:
        lookback_minutes: How far back to scan
        window_seconds: Cluster time window
        min_wallets: Minimum wallets per cluster
        db_path: Path to trades.db
        
    Returns:
        List of ExecutionClusterAlert objects
    """
    alerts = []
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)
    
    try:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            
            # Get distinct assets with multiple trades
            cursor = await db.execute(
                """
                SELECT DISTINCT asset_id
                FROM trades
                WHERE timestamp >= ?
                  AND owner_address IS NOT NULL
                GROUP BY asset_id
                HAVING COUNT(DISTINCT owner_address) >= ?
                """,
                (cutoff.isoformat(), min_wallets),
            )
            
            asset_rows = await cursor.fetchall()
            
            for row in asset_rows:
                asset_id = row["asset_id"]
                
                # Check each asset for execution clusters
                alert = await detect_execution_cluster(
                    asset_id=asset_id,
                    timestamp=datetime.now(timezone.utc),
                    window_seconds=window_seconds,
                    min_wallets=min_wallets,
                    db_path=db_path,
                )
                
                if alert:
                    alerts.append(alert)
    
    except Exception as e:
        print(f"[EXEC_CLUSTER] Scan error: {e}")
    
    return alerts
