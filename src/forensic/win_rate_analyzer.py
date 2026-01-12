"""
Win rate analyzer for Polymarket wallets.
Calculates historical win rate on positions using Moralis API.
"""
import aiohttp
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass

from ..config import (
    MORALIS_API_KEY,
    MORALIS_BASE_URL,
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
    Uses ERC-1155 token transfers to track position exits.
    """
    
    def __init__(self):
        pass
    
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
        if not MORALIS_API_KEY:
            return WinRateResult(
                win_rate=None,
                total_positions=0,
                winning_positions=0,
                total_volume_usdc=0,
                score_points=0,
                analysis_note="Moralis API key not configured",
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
            # This is a heuristic: large outflows to CTF Exchange suggest position exits
            cutoff_date = datetime.utcnow() - timedelta(days=lookback_days)
            
            positions = {}
            total_volume = 0.0
            
            for transfer in transfers:
                try:
                    tx_time = datetime.fromisoformat(
                        transfer.get("block_timestamp", "").replace("Z", "+00:00")
                    ).replace(tzinfo=None)
                except (ValueError, AttributeError):
                    continue
                
                if tx_time < cutoff_date:
                    continue
                
                token_id = transfer.get("token_id", "")
                amount = float(transfer.get("value", 0))
                to_addr = transfer.get("to_address", "").lower()
                from_addr = transfer.get("from_address", "").lower()
                
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
                    # If received USDC > initial position, likely a win
                    # This is simplified - full analysis would need pricing data
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
            print(f"⚠️ Error calculating win rate: {e}")
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
        """Fetch ERC-1155 transfers for an address from Moralis."""
        url = f"{MORALIS_BASE_URL}/{address}/nft/transfers"
        headers = {
            "accept": "application/json",
            "X-API-Key": MORALIS_API_KEY,
        }
        params = {
            "chain": "polygon",
            "limit": limit,
            "format": "decimal",
            "contract_addresses": [CTF_EXCHANGE_ADDRESS],
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("result", [])
                else:
                    error = await resp.text()
                    print(f"⚠️ Moralis NFT API error: {resp.status} - {error}")
                    return []
