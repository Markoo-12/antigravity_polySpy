"""
Bridge transaction detector for Polymarket wallets.
Checks if a wallet was funded via Across, Stargate, or Synapse bridges.
Uses PolygonScan/Etherscan V2 API.
"""
import aiohttp
from datetime import datetime, timedelta, timezone
from typing import Optional
from dataclasses import dataclass

from ..config import (
    POLYGONSCAN_API_KEY,
    POLYGONSCAN_BASE_URL,
    POLYGON_CHAIN_ID,
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
    Uses PolygonScan API instead of Moralis.
    """
    
    def __init__(self):
        self.bridge_addresses = {
            addr.lower(): name for name, addr in BRIDGE_CONTRACTS.items()
        }
        self.api_key = POLYGONSCAN_API_KEY
        self.base_url = POLYGONSCAN_BASE_URL
        self.chain_id = POLYGON_CHAIN_ID
    
    async def check_bridge_funding(
        self,
        owner_address: str,
        trade_timestamp: datetime,
        limit: int = 20
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
        if not self.api_key:
            print("[WARN] PolygonScan API key not configured, skipping bridge check")
            return BridgeDetectionResult(
                is_bridge_funded=False,
                bridge_name=None,
                bridge_tx_hash=None,
                hours_before_trade=None,
                score_points=0,
            )
        
        try:
            # Fetch recent transactions from PolygonScan
            transactions = await self._fetch_transactions(owner_address, limit)
            
            # Calculate time window
            if trade_timestamp.tzinfo is None:
                trade_timestamp = trade_timestamp.replace(tzinfo=timezone.utc)
            window_start = trade_timestamp - timedelta(hours=BRIDGE_TIME_WINDOW_HOURS)
            
            # Check each transaction for bridge origin
            for tx in transactions:
                tx_from = tx.get("from", "").lower()
                tx_timestamp = tx.get("timeStamp", "")
                
                # Check if from a bridge contract
                if tx_from in self.bridge_addresses:
                    # Parse timestamp (Unix timestamp)
                    try:
                        tx_time = datetime.fromtimestamp(int(tx_timestamp), tz=timezone.utc)
                    except (ValueError, TypeError):
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
            print(f"[WARN] Error checking bridge funding: {e}")
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
        limit: int = 20
    ) -> list[dict]:
        """Fetch recent transactions for an address from PolygonScan."""
        params = {
            "chainid": self.chain_id,
            "module": "account",
            "action": "txlist",
            "address": address,
            "startblock": 0,
            "endblock": 99999999,
            "page": 1,
            "offset": limit,
            "sort": "desc",  # Most recent first
            "apikey": self.api_key,
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.base_url, params=params, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("status") == "1" and data.get("result"):
                            return data["result"]
                        else:
                            return []
                    else:
                        print(f"[WARN] PolygonScan API error: {resp.status}")
                        return []
        except Exception as e:
            print(f"[WARN] PolygonScan fetch error: {e}")
            return []
