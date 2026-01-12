"""
Wallet Analyzer - Checks wallet age and activity patterns.
Uses Moralis API to detect new wallets and single-market whales.
"""
import aiohttp
from datetime import datetime, timedelta, timezone
from typing import Optional
from dataclasses import dataclass

from ..config import MORALIS_API_KEY, MORALIS_BASE_URL


# Scoring constants
SCORE_NEW_WALLET = 50  # Wallet < 7 days old
SCORE_LOW_TX_COUNT = 20  # Wallet < 10 transactions
SCORE_SINGLE_MARKET = 30  # 100% capital in one market (Maduro Rule)


@dataclass
class WalletAnalysisResult:
    """Result of wallet age and activity analysis."""
    first_tx_date: Optional[datetime] = None
    wallet_age_days: Optional[float] = None
    is_new_wallet: bool = False  # < 7 days old
    
    total_transactions: int = 0
    is_low_activity: bool = False  # < 10 transactions
    
    is_single_market: bool = False  # Maduro Rule
    
    score_points: int = 0
    analysis_note: str = ""


class WalletAnalyzer:
    """
    Analyzes wallet age and activity patterns to detect suspicious new accounts.
    
    Checks:
    1. Wallet age (< 7 days = +50 points)
    2. Transaction count (< 10 txns = +20 points)
    3. Single-market concentration (Maduro Rule = +30 points)
    """
    
    def __init__(self, api_key: str = MORALIS_API_KEY):
        self.api_key = api_key
        self.headers = {
            "Accept": "application/json",
            "X-API-Key": api_key,
        }
    
    async def analyze_wallet(
        self,
        wallet_address: str,
        trade_timestamp: Optional[datetime] = None,
        current_asset_id: Optional[str] = None,
    ) -> WalletAnalysisResult:
        """
        Analyze wallet for new wallet / single-market patterns.
        
        Args:
            wallet_address: The wallet address to analyze
            trade_timestamp: When the trade occurred (for age calculation)
            current_asset_id: The asset being traded (for Maduro Rule)
            
        Returns:
            WalletAnalysisResult with scoring
        """
        result = WalletAnalysisResult()
        score = 0
        notes = []
        
        if not self.api_key:
            result.analysis_note = "Moralis API key not configured"
            return result
        
        # Get wallet's first transaction date and activity
        try:
            chain_data = await self._get_wallet_active_chains(wallet_address)
            
            if chain_data:
                # Find Polygon chain data
                polygon_data = None
                for chain in chain_data.get("active_chains", []):
                    if chain.get("chain") == "polygon":
                        polygon_data = chain
                        break
                
                if polygon_data:
                    # Get first transaction date
                    first_tx = polygon_data.get("first_transaction", {})
                    first_tx_timestamp = first_tx.get("block_timestamp")
                    
                    if first_tx_timestamp:
                        # Parse ISO format timestamp
                        result.first_tx_date = datetime.fromisoformat(
                            first_tx_timestamp.replace("Z", "+00:00")
                        )
                        
                        # Calculate wallet age
                        ref_time = trade_timestamp or datetime.now(timezone.utc)
                        if ref_time.tzinfo is None:
                            ref_time = ref_time.replace(tzinfo=timezone.utc)
                        
                        age = ref_time - result.first_tx_date
                        result.wallet_age_days = age.total_seconds() / 86400
                        
                        # Check if new wallet (< 7 days)
                        if result.wallet_age_days < 7:
                            result.is_new_wallet = True
                            score += SCORE_NEW_WALLET
                            notes.append(f"New wallet ({result.wallet_age_days:.1f} days old) [+{SCORE_NEW_WALLET}]")
            
            # Get transaction count
            tx_stats = await self._get_wallet_stats(wallet_address)
            if tx_stats:
                result.total_transactions = tx_stats.get("transactions", {}).get("total", 0)
                
                if result.total_transactions < 10:
                    result.is_low_activity = True
                    score += SCORE_LOW_TX_COUNT
                    notes.append(f"Low activity ({result.total_transactions} txns) [+{SCORE_LOW_TX_COUNT}]")
            
            # Check for single-market concentration (Maduro Rule)
            if current_asset_id:
                is_single = await self._check_single_market(wallet_address, current_asset_id)
                if is_single:
                    result.is_single_market = True
                    score += SCORE_SINGLE_MARKET
                    notes.append(f"Single-market whale (Maduro Rule) [+{SCORE_SINGLE_MARKET}]")
        
        except Exception as e:
            notes.append(f"Analysis error: {str(e)[:50]}")
        
        result.score_points = score
        result.analysis_note = "; ".join(notes) if notes else "No new wallet flags"
        
        return result
    
    async def _get_wallet_active_chains(self, wallet_address: str) -> Optional[dict]:
        """
        Get wallet's active chains with first transaction dates.
        Uses Moralis getWalletActiveChains endpoint.
        """
        url = f"{MORALIS_BASE_URL}/wallets/{wallet_address}/chains"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self.headers) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    else:
                        error = await resp.text()
                        print(f"[WALLET] Moralis chains error: {resp.status}")
                        return None
        except Exception as e:
            print(f"[WALLET] API error: {e}")
            return None
    
    async def _get_wallet_stats(self, wallet_address: str) -> Optional[dict]:
        """
        Get wallet statistics including transaction count.
        Uses Moralis getWalletStats endpoint.
        """
        url = f"{MORALIS_BASE_URL}/wallets/{wallet_address}/stats"
        params = {"chain": "polygon"}
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self.headers, params=params) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    return None
        except Exception as e:
            print(f"[WALLET] Stats error: {e}")
            return None
    
    async def _check_single_market(
        self,
        wallet_address: str,
        current_asset_id: str
    ) -> bool:
        """
        Check if wallet only trades in a single market (Maduro Rule).
        Looks at ERC-1155 holdings to see if all positions are in one asset.
        """
        url = f"{MORALIS_BASE_URL}/{wallet_address}/nft"
        params = {
            "chain": "polygon",
            "format": "decimal",
            "token_addresses": ["0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"],  # CTF token
            "limit": 50
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self.headers, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        nfts = data.get("result", [])
                        
                        if not nfts:
                            return False
                        
                        # Get unique token IDs (asset IDs)
                        asset_ids = set()
                        for nft in nfts:
                            token_id = nft.get("token_id")
                            if token_id:
                                asset_ids.add(token_id)
                        
                        # Maduro Rule: Only one asset ID
                        if len(asset_ids) == 1 and current_asset_id in asset_ids:
                            return True
                        
                        # Also flag if current asset is >90% of holdings
                        if len(asset_ids) <= 2:
                            return True
                        
                        return False
                    return False
        except Exception as e:
            print(f"[WALLET] Single market check error: {e}")
            return False
