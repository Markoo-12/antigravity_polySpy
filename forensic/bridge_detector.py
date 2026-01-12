"""
Bridge transaction detector for Polymarket wallets.
Checks if a wallet was funded via Across, Stargate, or Synapse bridges.
"""
import aiohttp
from datetime import datetime, timedelta
from typing import Optional, Tuple
from dataclasses import dataclass

from ..config import (
    MORALIS_API_KEY,
    MORALIS_BASE_URL,
    BRIDGE_CONTRACTS,
    BRIDGE_TIME_WINDOW_HOURS,
    SCORE_BRIDGE_FUNDED,
)


@dataclass
class BridgeDetectionResult:
    """Result of bridge detection analysis."""
    is_bridge_funded: bool
    bridge_name: Optional[str]
    bridge_tx_hash: Optional[str]
    hours_before_trade: Optional[float]
    score_points: int


class BridgeDetector:
    """
    Detects if a wallet received funds from a bridge contract
    within a specified time window before a trade.
    """
    
    def __init__(self):
        self.bridge_addresses = {
            addr.lower(): name for name, addr in BRIDGE_CONTRACTS.items()
        }
    
    async def check_bridge_funding(
        self,
        owner_address: str,
        trade_timestamp: datetime,
        limit: int = 10
    ) -> BridgeDetectionResult:
        """
        Check if the owner wallet received funds from a bridge
        within BRIDGE_TIME_WINDOW_HOURS before the trade.
        
        Args:
            owner_address: The owner EOA address
            trade_timestamp: When the trade occurred
            limit: Number of recent transactions to check
            
        Returns:
            BridgeDetectionResult with detection info
        """
        if not MORALIS_API_KEY:
            print("⚠️ Moralis API key not configured, skipping bridge check")
            return BridgeDetectionResult(
                is_bridge_funded=False,
                bridge_name=None,
                bridge_tx_hash=None,
                hours_before_trade=None,
                score_points=0,
            )
        
        try:
            # Fetch recent transactions from Moralis
            transactions = await self._fetch_transactions(owner_address, limit)
            
            # Calculate time window
            window_start = trade_timestamp - timedelta(hours=BRIDGE_TIME_WINDOW_HOURS)
            
            # Check each transaction for bridge origin
            for tx in transactions:
                tx_from = tx.get("from_address", "").lower()
                tx_time_str = tx.get("block_timestamp", "")
                
                # Check if from a bridge contract
                if tx_from in self.bridge_addresses:
                    # Parse timestamp
                    try:
                        tx_time = datetime.fromisoformat(tx_time_str.replace("Z", "+00:00"))
                        tx_time = tx_time.replace(tzinfo=None)  # Make naive for comparison
                    except (ValueError, AttributeError):
                        continue
                    
                    # Check if within time window
                    if window_start <= tx_time <= trade_timestamp:
                        hours_diff = (trade_timestamp - tx_time).total_seconds() / 3600
                        bridge_name = self.bridge_addresses[tx_from]
                        
                        return BridgeDetectionResult(
                            is_bridge_funded=True,
                            bridge_name=bridge_name,
                            bridge_tx_hash=tx.get("hash"),
                            hours_before_trade=round(hours_diff, 2),
                            score_points=SCORE_BRIDGE_FUNDED,
                        )
            
            # No bridge funding detected
            return BridgeDetectionResult(
                is_bridge_funded=False,
                bridge_name=None,
                bridge_tx_hash=None,
                hours_before_trade=None,
                score_points=0,
            )
            
        except Exception as e:
            print(f"⚠️ Error checking bridge funding: {e}")
            return BridgeDetectionResult(
                is_bridge_funded=False,
                bridge_name=None,
                bridge_tx_hash=None,
                hours_before_trade=None,
                score_points=0,
            )
    
    async def _fetch_transactions(
        self,
        address: str,
        limit: int = 10
    ) -> list[dict]:
        """Fetch recent transactions for an address from Moralis."""
        url = f"{MORALIS_BASE_URL}/{address}"
        headers = {
            "accept": "application/json",
            "X-API-Key": MORALIS_API_KEY,
        }
        params = {
            "chain": "polygon",
            "limit": limit,
            "order": "DESC",
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("result", [])
                else:
                    error = await resp.text()
                    print(f"⚠️ Moralis API error: {resp.status} - {error}")
                    return []
