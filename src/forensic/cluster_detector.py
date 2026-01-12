"""
Cluster Detector - Detects coordinated trading by multiple fresh wallets.

Maintains a rolling window of high-score trades and triggers a
CONVICTION CLUSTER alert when 3+ fresh wallets enter the same position
within 5 minutes.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, List, Dict
from collections import defaultdict

from ..config import (
    CLUSTER_WINDOW_MINUTES,
    CLUSTER_THRESHOLD_WALLETS,
    CLUSTER_TIME_WINDOW_MINUTES,
    CLUSTER_MIN_SCORE,
)


@dataclass
class ClusterTrade:
    """A trade in the rolling window."""
    wallet_address: str
    asset_id: str
    insider_score: int
    trade_amount_usdc: float
    timestamp: datetime
    side: str  # 'buy' or 'sell'
    tx_hash: str


@dataclass
class ClusterAlert:
    """Alert when a conviction cluster is detected."""
    asset_id: str
    wallets: List[str]
    total_amount_usdc: float
    avg_score: float
    time_span_seconds: int
    first_trade: datetime
    last_trade: datetime
    trades: List[ClusterTrade] = field(default_factory=list)


class ClusterDetector:
    """
    Detects coordinated trading by multiple high-score wallets.
    
    Logic:
    - Maintains a 10-minute rolling window of all high-score trades
    - If 3+ different wallets (score >= 70) enter the same position
      within 5 minutes, triggers a CONVICTION CLUSTER alert
    """
    
    def __init__(self):
        self.window_duration = timedelta(minutes=CLUSTER_WINDOW_MINUTES)
        self.cluster_time_window = timedelta(minutes=CLUSTER_TIME_WINDOW_MINUTES)
        self.cluster_threshold = CLUSTER_THRESHOLD_WALLETS
        self.min_score = CLUSTER_MIN_SCORE
        
        # Rolling window: asset_id -> list of ClusterTrades
        self.trades_by_asset: Dict[str, List[ClusterTrade]] = defaultdict(list)
        
        # Track detected clusters to avoid duplicate alerts
        self._alerted_clusters: set = set()
    
    def add_trade(
        self,
        wallet_address: str,
        asset_id: str,
        insider_score: int,
        trade_amount_usdc: float,
        side: str,
        tx_hash: str,
        timestamp: Optional[datetime] = None,
    ) -> Optional[ClusterAlert]:
        """
        Add a trade to the rolling window and check for clusters.
        
        Args:
            wallet_address: The resolved owner EOA
            asset_id: The outcome token ID
            insider_score: Calculated insider score
            trade_amount_usdc: Trade size in USDC
            side: 'buy' or 'sell'
            tx_hash: Transaction hash
            timestamp: Trade timestamp (defaults to now)
            
        Returns:
            ClusterAlert if a cluster is detected, None otherwise
        """
        if timestamp is None:
            timestamp = datetime.utcnow()
        
        # Only track high-score trades
        if insider_score < self.min_score:
            return None
        
        # Clean old trades from window
        self._clean_window()
        
        # Add new trade
        trade = ClusterTrade(
            wallet_address=wallet_address,
            asset_id=asset_id,
            insider_score=insider_score,
            trade_amount_usdc=trade_amount_usdc,
            timestamp=timestamp,
            side=side,
            tx_hash=tx_hash,
        )
        self.trades_by_asset[asset_id].append(trade)
        
        # Check for cluster
        return self._detect_cluster(asset_id)
    
    def _clean_window(self) -> None:
        """Remove trades older than the window duration."""
        cutoff = datetime.utcnow() - self.window_duration
        
        for asset_id in list(self.trades_by_asset.keys()):
            self.trades_by_asset[asset_id] = [
                t for t in self.trades_by_asset[asset_id]
                if t.timestamp >= cutoff
            ]
            
            # Remove empty lists
            if not self.trades_by_asset[asset_id]:
                del self.trades_by_asset[asset_id]
    
    def _detect_cluster(self, asset_id: str) -> Optional[ClusterAlert]:
        """
        Check if trades for an asset form a cluster.
        
        A cluster is detected when:
        - 3+ unique wallets
        - All within 5-minute time span
        - All with score >= 70
        """
        trades = self.trades_by_asset.get(asset_id, [])
        
        if len(trades) < self.cluster_threshold:
            return None
        
        # Group by unique wallets
        unique_wallets = set(t.wallet_address for t in trades)
        
        if len(unique_wallets) < self.cluster_threshold:
            return None
        
        # Check time span - find trades within cluster_time_window
        trades_sorted = sorted(trades, key=lambda t: t.timestamp)
        
        # Sliding window to find cluster
        for i in range(len(trades_sorted)):
            window_trades = []
            window_wallets = set()
            
            for j in range(i, len(trades_sorted)):
                time_diff = trades_sorted[j].timestamp - trades_sorted[i].timestamp
                
                if time_diff <= self.cluster_time_window:
                    # Only add if we haven't seen this wallet in this window
                    if trades_sorted[j].wallet_address not in window_wallets:
                        window_trades.append(trades_sorted[j])
                        window_wallets.add(trades_sorted[j].wallet_address)
                else:
                    break
            
            # Check if we have a cluster
            if len(window_wallets) >= self.cluster_threshold:
                # Generate cluster key to avoid duplicate alerts
                cluster_key = f"{asset_id}:{trades_sorted[i].timestamp.isoformat()}"
                
                if cluster_key in self._alerted_clusters:
                    continue
                
                self._alerted_clusters.add(cluster_key)
                
                # Build alert
                total_amount = sum(t.trade_amount_usdc for t in window_trades)
                avg_score = sum(t.insider_score for t in window_trades) / len(window_trades)
                time_span = int((window_trades[-1].timestamp - window_trades[0].timestamp).total_seconds())
                
                alert = ClusterAlert(
                    asset_id=asset_id,
                    wallets=list(window_wallets),
                    total_amount_usdc=total_amount,
                    avg_score=avg_score,
                    time_span_seconds=time_span,
                    first_trade=window_trades[0].timestamp,
                    last_trade=window_trades[-1].timestamp,
                    trades=window_trades,
                )
                
                print(f"[CLUSTER] CONVICTION CLUSTER detected: {len(window_wallets)} wallets on {asset_id[:16]}...")
                
                return alert
        
        return None
    
    def get_stats(self) -> Dict[str, int]:
        """Get current detector statistics."""
        return {
            "assets_tracked": len(self.trades_by_asset),
            "total_trades": sum(len(t) for t in self.trades_by_asset.values()),
            "clusters_alerted": len(self._alerted_clusters),
        }
    
    def clear(self) -> None:
        """Clear all tracked trades and alerts."""
        self.trades_by_asset.clear()
        self._alerted_clusters.clear()
