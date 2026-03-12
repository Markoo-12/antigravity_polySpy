"""
Wallet Analyzer - Checks wallet age and activity patterns.
Uses PolygonScan/Etherscan V2 API to detect new wallets and single-market whales.
"""
import aiohttp
from datetime import datetime, timezone
from typing import Optional
from dataclasses import dataclass

from ..config import (
    POLYGONSCAN_API_KEY,
    POLYGONSCAN_BASE_URL,
    POLYGON_CHAIN_ID,
    CTF_EXCHANGE_ADDRESS,
)


# Scoring constants
SCORE_NEW_WALLET = 50  # Wallet < 72 hours old = +50 flat
SCORE_LOW_TX_COUNT = 20  # Wallet < 20 transactions
SCORE_SINGLE_MARKET = 30  # 100% capital in one market (Maduro Rule)
SCORE_ROUND_NUMBER = 15  # Trade amount is clean multiple of 1000/5000/10000
SCORE_BINARY_CONCENTRATION = 25  # 100% of Polymarket balance in single asset

# Wallet age threshold (Phase 2 spec: <72 hours)
WALLET_AGE_MAX_HOURS = 72  # Wallets younger than this get full bonus
WALLET_AGE_MAX_DAYS = 3.0  # 72 hours = 3 days

# Low activity threshold (Phase 2 spec: <20 txns)
LOW_ACTIVITY_THRESHOLD = 20  # Transactions below this = low activity


def check_round_number(amount_usdc: float) -> bool:
    """Check if amount is a 'round' number (psychological bias indicator)."""
    if amount_usdc <= 0:
        return False
    return amount_usdc % 1000 == 0


def calculate_wallet_age_score(age_days: float) -> int:
    """
    Calculate wallet age score.
    Phase 2 Spec: If wallet < 72 hours old, +50 flat.
    """
    if age_days is None:
        return 0
    if age_days < WALLET_AGE_MAX_DAYS:
        return SCORE_NEW_WALLET  # +50 flat
    return 0


@dataclass
class WalletAnalysisResult:
    """Result of wallet age and activity analysis."""
    first_tx_date: Optional[datetime] = None
    wallet_age_days: Optional[float] = None
    is_new_wallet: bool = False  # < 72 hours old
    wallet_age_score: int = 0
    
    total_transactions: int = 0
    is_low_activity: bool = False  # < 20 transactions
    
    is_single_market: bool = False  # Maduro Rule
    is_round_number: bool = False  # Round number bias
    is_binary_concentration: bool = False  # 100% in single asset
    
    score_points: int = 0
    analysis_note: str = ""


class WalletAnalyzer:
    """
    Analyzes wallet age and activity patterns using PolygonScan API.
    
    Checks:
    1. Wallet age (< 72 hours = +50 points)
    2. Transaction count (< 20 txns = +20 points)
    3. Single-market concentration (Maduro Rule = +30 points)
    4. Round number trades (multiples of 1000 = +15 points)
    """
    
    def __init__(self):
        self.api_key = POLYGONSCAN_API_KEY
        self.base_url = POLYGONSCAN_BASE_URL
        self.chain_id = POLYGON_CHAIN_ID
    
    async def analyze_wallet(
        self,
        wallet_address: str,
        trade_timestamp: Optional[datetime] = None,
        current_asset_id: Optional[str] = None,
        trade_amount_usdc: Optional[float] = None,
    ) -> WalletAnalysisResult:
        """
        Analyze wallet for new wallet / single-market patterns.
        """
        result = WalletAnalysisResult()
        score = 0
        notes = []
        
        if not self.api_key:
            result.analysis_note = "PolygonScan API key not configured"
            return result
        
        try:
            # Get first transaction and transaction count
            tx_data = await self._get_wallet_transactions(wallet_address)
            
            if tx_data:
                # Calculate wallet age from first transaction
                if tx_data.get("first_tx_timestamp"):
                    first_tx_ts = int(tx_data["first_tx_timestamp"])
                    result.first_tx_date = datetime.fromtimestamp(first_tx_ts, tz=timezone.utc)
                    
                    ref_time = trade_timestamp or datetime.now(timezone.utc)
                    if ref_time.tzinfo is None:
                        ref_time = ref_time.replace(tzinfo=timezone.utc)
                    
                    age = ref_time - result.first_tx_date
                    result.wallet_age_days = age.total_seconds() / 86400
                    
                    # Check if new wallet (< 72 hours)
                    if result.wallet_age_days < WALLET_AGE_MAX_DAYS:
                        result.is_new_wallet = True
                        age_score = calculate_wallet_age_score(result.wallet_age_days)
                        result.wallet_age_score = age_score
                        score += age_score
                        notes.append(f"New wallet ({result.wallet_age_days:.1f} days old) [+{age_score}]")
                
                # Transaction count
                result.total_transactions = tx_data.get("tx_count", 0)
                
                if result.total_transactions < LOW_ACTIVITY_THRESHOLD:
                    result.is_low_activity = True
                    score += SCORE_LOW_TX_COUNT
                    notes.append(f"Low activity ({result.total_transactions} txns < {LOW_ACTIVITY_THRESHOLD}) [+{SCORE_LOW_TX_COUNT}]")
            
            # Check for single-market concentration (Maduro Rule)
            if current_asset_id:
                is_single = await self._check_single_market(wallet_address, current_asset_id)
                if is_single:
                    result.is_single_market = True
                    score += SCORE_SINGLE_MARKET
                    notes.append(f"Single-market whale (Maduro Rule) [+{SCORE_SINGLE_MARKET}]")
            
            # Check for round number trades
            if trade_amount_usdc and check_round_number(trade_amount_usdc):
                result.is_round_number = True
                score += SCORE_ROUND_NUMBER
                notes.append(f"Round number trade (${trade_amount_usdc:,.0f}) [+{SCORE_ROUND_NUMBER}]")
            
            # Check for binary concentration
            if current_asset_id:
                is_binary = await self._check_binary_concentration(wallet_address, current_asset_id)
                if is_binary:
                    result.is_binary_concentration = True
                    score += SCORE_BINARY_CONCENTRATION
                    notes.append(f"Binary concentration (100% in single asset) [+{SCORE_BINARY_CONCENTRATION}]")
        
        except Exception as e:
            notes.append(f"Analysis error: {str(e)[:50]}")
        
        result.score_points = score
        result.analysis_note = "; ".join(notes) if notes else "No wallet flags"
        
        return result
    
    async def _get_wallet_transactions(self, wallet_address: str) -> Optional[dict]:
        """
        Get wallet's first transaction and transaction count using PolygonScan.
        """
        try:
            async with aiohttp.ClientSession() as session:
                # Get first transaction (sorted ascending)
                params = {
                    "chainid": self.chain_id,
                    "module": "account",
                    "action": "txlist",
                    "address": wallet_address,
                    "startblock": 0,
                    "endblock": 99999999,
                    "page": 1,
                    "offset": 1,  # Just get first tx
                    "sort": "asc",
                    "apikey": self.api_key,
                }
                
                async with session.get(self.base_url, params=params, timeout=10) as resp:
                    if resp.status != 200:
                        return None
                    
                    data = await resp.json()
                    first_tx_timestamp = None
                    
                    if data.get("status") == "1" and data.get("result"):
                        first_tx = data["result"][0]
                        first_tx_timestamp = first_tx.get("timeStamp")
                
                # Get total transaction count
                params_count = {
                    "chainid": self.chain_id,
                    "module": "proxy",
                    "action": "eth_getTransactionCount",
                    "address": wallet_address,
                    "tag": "latest",
                    "apikey": self.api_key,
                }
                
                async with session.get(self.base_url, params=params_count, timeout=10) as resp:
                    tx_count = 0
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("result"):
                            # Convert hex to int
                            tx_count = int(data["result"], 16)
                
                return {
                    "first_tx_timestamp": first_tx_timestamp,
                    "tx_count": tx_count,
                }
        
        except Exception as e:
            print(f"[WARN] PolygonScan wallet lookup error: {e}")
            return None
    
    async def _check_single_market(
        self,
        wallet_address: str,
        current_asset_id: str
    ) -> bool:
        """
        Check if wallet only trades in a single market (Maduro Rule).
        Uses ERC-1155 transfer events to CTF contract.
        """
        try:
            params = {
                "chainid": self.chain_id,
                "module": "account",
                "action": "token1155tx",  # ERC-1155 transfers
                "address": wallet_address,
                "contractaddress": CTF_EXCHANGE_ADDRESS,
                "page": 1,
                "offset": 50,
                "sort": "desc",
                "apikey": self.api_key,
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(self.base_url, params=params, timeout=10) as resp:
                    if resp.status != 200:
                        return False
                    
                    data = await resp.json()
                    
                    if data.get("status") == "1" and data.get("result"):
                        transfers = data["result"]
                        
                        # Get unique token IDs
                        token_ids = set()
                        for tx in transfers:
                            token_id = tx.get("tokenID")
                            if token_id:
                                token_ids.add(token_id)
                        
                        # Maduro Rule: Exactly 1 token ID and it IS the current asset
                        if len(token_ids) == 1 and current_asset_id in token_ids:
                            return True
                    
                    return False
        
        except Exception as e:
            print(f"[WARN] Single market check error: {e}")
            return False
    
    async def _check_binary_concentration(
        self,
        wallet_address: str,
        current_asset_id: str
    ) -> bool:
        """
        Check if wallet has 100% of Polymarket balance in a single asset.
        """
        try:
            params = {
                "chainid": self.chain_id,
                "module": "account",
                "action": "token1155tx",
                "address": wallet_address,
                "contractaddress": CTF_EXCHANGE_ADDRESS,
                "page": 1,
                "offset": 50,
                "sort": "desc",
                "apikey": self.api_key,
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(self.base_url, params=params, timeout=10) as resp:
                    if resp.status != 200:
                        return False
                    
                    data = await resp.json()
                    
                    if data.get("status") == "1" and data.get("result"):
                        transfers = data["result"]
                        
                        # Calculate net holdings per token
                        holdings = {}
                        for tx in transfers:
                            token_id = tx.get("tokenID")
                            value = int(tx.get("tokenValue", 0))
                            to_addr = tx.get("to", "").lower()
                            
                            if token_id not in holdings:
                                holdings[token_id] = 0
                            
                            if to_addr == wallet_address.lower():
                                holdings[token_id] += value
                            else:
                                holdings[token_id] -= value
                        
                        # Filter to positive holdings
                        active_holdings = {k: v for k, v in holdings.items() if v > 0}
                        
                        # Binary concentration: exactly 1 active holding
                        if len(active_holdings) == 1 and current_asset_id in active_holdings:
                            return True
                    
                    return False
        
        except Exception as e:
            print(f"[WARN] Binary concentration check error: {e}")
            return False
