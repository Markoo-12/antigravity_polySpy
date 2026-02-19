"""
Market Resolver - Fetches market metadata from Polymarket Gamma API.

Provides market slug for direct execution links in Telegram alerts.
"""
import aiohttp
from dataclasses import dataclass
from typing import Optional
from functools import lru_cache
import asyncio


@dataclass
class MarketInfo:
    """Market metadata from Gamma API."""
    slug: Optional[str] = None
    question: Optional[str] = None
    condition_id: Optional[str] = None
    outcome: Optional[str] = None  # 'Yes' or 'No' based on token
    
    @property
    def execution_url(self) -> Optional[str]:
        """Get the direct Polymarket execution URL."""
        if self.slug:
            return f"https://polymarket.com/event/{self.slug}"
        return None


class MarketResolver:
    """
    Resolves asset/token IDs to market metadata.
    
    Uses the Gamma API to fetch market slugs for building
    direct execution links in Telegram alerts.
    """
    
    GAMMA_API_URL = "https://gamma-api.polymarket.com"
    
    def __init__(self):
        # Simple in-memory cache to avoid repeated API calls
        self._cache: dict[str, MarketInfo] = {}
    
    async def resolve(self, asset_id: str) -> MarketInfo:
        """
        Resolve an asset/token ID to market metadata.
        
        Args:
            asset_id: The CLOB token ID (outcome token)
            
        Returns:
            MarketInfo with slug, question, and outcome
        """
        # Check cache first
        if asset_id in self._cache:
            return self._cache[asset_id]
        
        market_info = await self._fetch_from_gamma(asset_id)
        
        # Cache the result
        if market_info.slug:
            self._cache[asset_id] = market_info
        
        return market_info
    
    async def _fetch_from_gamma(self, asset_id: str) -> MarketInfo:
        """Fetch market info from Gamma API."""
        url = f"{self.GAMMA_API_URL}/markets"
        params = {"clob_token_ids": asset_id}
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=10) as resp:
                    if resp.status == 200:
                        markets = await resp.json()
                        
                        if markets and len(markets) > 0:
                            market = markets[0]
                            
                            # Determine outcome (Yes/No) based on token position
                            # tokens[0] = Yes, tokens[1] = No typically
                            outcome = None
                            tokens = market.get("clobTokenIds") or market.get("clob_token_ids", [])
                            
                            # Handle stringified list from API
                            if isinstance(tokens, str):
                                try:
                                    import json
                                    tokens = json.loads(tokens)
                                except:
                                    pass

                            if tokens and isinstance(tokens, list):
                                if len(tokens) >= 2:
                                    if asset_id == tokens[0]:
                                        outcome = "Yes"
                                    elif asset_id == tokens[1]:
                                        outcome = "No"
                            
                            return MarketInfo(
                                slug=market.get("slug"),
                                question=market.get("question"),
                                condition_id=market.get("conditionId") or market.get("condition_id"),
                                outcome=outcome,
                            )
                        
                        return MarketInfo()
                    else:
                        print(f"[WARN] Gamma API returned {resp.status} for asset {asset_id[:16]}...")
                        return MarketInfo()
                        
        except asyncio.TimeoutError:
            print(f"[WARN] Gamma API timeout for asset {asset_id[:16]}...")
            return MarketInfo()
        except Exception as e:
            print(f"[WARN] Failed to resolve market: {e}")
            return MarketInfo()
    
    @property
    def cache_size(self) -> int:
        """Return number of cached market lookups."""
        return len(self._cache)
