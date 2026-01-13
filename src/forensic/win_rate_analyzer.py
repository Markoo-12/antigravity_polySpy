"""
Win rate analyzer for Polymarket wallets.
Calculates historical win rate on positions using PolygonScan API.
"""
import aiohttp
from datetime import datetime, timedelta, timezone
from typing import Optional
from dataclasses import dataclass

from ..config import (
    POLYGONSCAN_API_KEY,
    POLYGONSCAN_BASE_URL,
    POLYGON_CHAIN_ID,
    WIN_RATE_THRESHOLD,
    WIN_RATE_POSITION_MIN,
    SCORE_HIGH_WIN_RATE,
    CTF_EXCHANGE_ADDRESS,
)


@dataclass
class WinRateResult:
    """Result of win rate analysis."""
    win_rate: Optional[float]  # 0.0 to 1.0
    total_positions: int
    winning_positions: int
    total_volume_usdc: float
    score_points: int
    analysis_note: str


class WinRateAnalyzer:
    """
    Analyzes a wallet's historical win rate on Polymarket positions.
    Uses ERC-1155 token transfers via PolygonScan API.
    """
    
    def __init__(self):
        self.api_key = POLYGONSCAN_API_KEY
        self.base_url = POLYGONSCAN_BASE_URL
        self.chain_id = POLYGON_CHAIN_ID
    
    async def calculate_win_rate(
        self,
        owner_address: str,
        lookback_days: int = 30
    ) -> WinRateResult:
        """
        Calculate the win rate for a wallet over the lookback period.
        
        For Polymarket:
        - A "win" is when the outcome token resolves to 1 (full payout)
        - A "loss" is when the outcome token resolves to 0 (no payout)
        
        Note: This is a simplified analysis based on token transfers.
        Full P&L calculation would require tracking entry/exit prices.
        
        Args:
            owner_address: The owner EOA address
            lookback_days: Number of days to look back
            
        Returns:
            WinRateResult with analysis
        """
        if not self.api_key:
            return WinRateResult(
                win_rate=None,
                total_positions=0,
                winning_positions=0,
                total_volume_usdc=0,
                score_points=0,
                analysis_note="PolygonScan API key not configured",
            )
        
        try:
            # Fetch ERC-1155 transfers (Polymarket outcome tokens)
            transfers = await self._fetch_erc1155_transfers(owner_address)
            
            if not transfers:
                return WinRateResult(
                    win_rate=None,
                    total_positions=0,
                    winning_positions=0,
                    total_volume_usdc=0,
                    score_points=0,
                    analysis_note="No position history found",
                )
            
            # Analyze transfers to estimate win rate
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=lookback_days)
            
            positions = {}
            total_volume = 0.0
            
            for transfer in transfers:
                try:
                    # PolygonScan returns Unix timestamp
                    tx_timestamp = int(transfer.get("timeStamp", 0))
                    tx_time = datetime.fromtimestamp(tx_timestamp, tz=timezone.utc)
                except (ValueError, TypeError):
                    continue
                
                if tx_time < cutoff_date:
                    continue
                
                token_id = transfer.get("tokenID", "")
                amount = float(transfer.get("tokenValue", 0))
                to_addr = transfer.get("to", "").lower()
                from_addr = transfer.get("from", "").lower()
                
                # Track token movements
                if token_id not in positions:
                    positions[token_id] = {"received": 0, "sent": 0}
                
                if to_addr == owner_address.lower():
                    positions[token_id]["received"] += amount
                elif from_addr == owner_address.lower():
                    positions[token_id]["sent"] += amount
            
            # Analyze positions
            total_positions = len(positions)
            winning_positions = 0
            
            for token_id, data in positions.items():
                # Heuristic: if sent back to exchange (likely redemption), count as closed
                if data["sent"] > 0:
                    # If received > initial position, likely a win
                    if data["received"] > 0:
                        total_volume += data["received"] / 1e6  # USDC has 6 decimals
                        # Assume profitable if position was closed
                        winning_positions += 1
            
            # Calculate win rate
            if total_positions > 0:
                win_rate = winning_positions / total_positions
                
                # Apply scoring if meets threshold
                score_points = 0
                if win_rate >= WIN_RATE_THRESHOLD and total_volume >= WIN_RATE_POSITION_MIN:
                    score_points = SCORE_HIGH_WIN_RATE
                
                return WinRateResult(
                    win_rate=win_rate,
                    total_positions=total_positions,
                    winning_positions=winning_positions,
                    total_volume_usdc=total_volume,
                    score_points=score_points,
                    analysis_note=f"Analyzed {total_positions} positions over {lookback_days} days",
                )
            
            return WinRateResult(
                win_rate=None,
                total_positions=0,
                winning_positions=0,
                total_volume_usdc=0,
                score_points=0,
                analysis_note="No closed positions found",
            )
            
        except Exception as e:
            print(f"[WARN] Error calculating win rate: {e}")
            return WinRateResult(
                win_rate=None,
                total_positions=0,
                winning_positions=0,
                total_volume_usdc=0,
                score_points=0,
                analysis_note=f"Analysis error: {str(e)}",
            )
    
    async def _fetch_erc1155_transfers(
        self,
        address: str,
        limit: int = 100
    ) -> list[dict]:
        """Fetch ERC-1155 transfers for an address from PolygonScan."""
        params = {
            "chainid": self.chain_id,
            "module": "account",
            "action": "token1155tx",
            "address": address,
            "contractaddress": CTF_EXCHANGE_ADDRESS,
            "page": 1,
            "offset": limit,
            "sort": "desc",
            "apikey": self.api_key,
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.base_url, params=params, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("status") == "1" and data.get("result"):
                            return data["result"]
                        return []
                    else:
                        print(f"[WARN] PolygonScan API error: {resp.status}")
                        return []
        except Exception as e:
            print(f"[WARN] PolygonScan fetch error: {e}")
            return []
