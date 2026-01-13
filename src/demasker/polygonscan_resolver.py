"""
PolygonScan API resolver for contract creators.
Uses contract creation lookup to find who deployed a proxy contract.
"""
import aiohttp
from typing import Optional

from ..config import POLYGONSCAN_API_KEY, POLYGONSCAN_BASE_URL, POLYGON_CHAIN_ID


class PolygonScanResolver:
    """
    Resolve proxy contract owner by finding who created the contract.
    
    Uses PolygonScan's getcontractcreation API to find the deployer,
    which for Polymarket proxies is typically the user's EOA.
    
    Free tier: 5 calls/sec, 100k calls/day
    """
    
    def __init__(self):
        self.api_key = POLYGONSCAN_API_KEY
        self.base_url = POLYGONSCAN_BASE_URL
        self._cache: dict[str, Optional[str]] = {}
    
    async def get_creator(self, contract_address: str) -> Optional[str]:
        """
        Get the creator (deployer) of a contract.
        
        For Polymarket EIP-1167 proxies, the creator is typically
        the Polymarket factory. We then need to parse the creation
        transaction to find the actual owner.
        
        Args:
            contract_address: The proxy contract address
            
        Returns:
            The creator EOA address, or None if lookup fails
        """
        # Check cache
        if contract_address in self._cache:
            return self._cache[contract_address]
        
        if not self.api_key:
            print("[WARN] PolygonScan API key not configured")
            return None
        
        try:
            url = self.base_url
            params = {
                "chainid": POLYGON_CHAIN_ID,  # V2 API requires chainid
                "module": "contract",
                "action": "getcontractcreation",
                "contractaddresses": contract_address,
                "apikey": self.api_key,
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        
                        if data.get("status") == "1" and data.get("result"):
                            result = data["result"][0]
                            creator = result.get("contractCreator")
                            tx_hash = result.get("txHash")
                            
                            if creator:
                                # Cache and return
                                self._cache[contract_address] = creator
                                print(f"   [POLYGONSCAN] Creator found: {creator[:20]}...")
                                return creator
                        else:
                            message = data.get("message", "Unknown error")
                            print(f"   [WARN] PolygonScan: {message}")
                    else:
                        print(f"   [WARN] PolygonScan HTTP error: {resp.status}")
            
        except Exception as e:
            print(f"   [WARN] PolygonScan error: {e}")
        
        self._cache[contract_address] = None
        return None
    
    async def get_first_funder(self, contract_address: str) -> Optional[str]:
        """
        Alternative: Get the first address that sent funds to the contract.
        This is often the owner for wallet proxies.
        
        Args:
            contract_address: The contract address
            
        Returns:
            The first funder address, or None
        """
        if not self.api_key:
            return None
        
        try:
            url = self.base_url
            params = {
                "chainid": POLYGON_CHAIN_ID,  # V2 API requires chainid
                "module": "account",
                "action": "txlist",
                "address": contract_address,
                "startblock": 0,
                "endblock": 99999999,
                "page": 1,
                "offset": 1,  # Only get first transaction
                "sort": "asc",
                "apikey": self.api_key,
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        
                        if data.get("status") == "1" and data.get("result"):
                            first_tx = data["result"][0]
                            sender = first_tx.get("from")
                            
                            if sender:
                                print(f"   [POLYGONSCAN] First funder: {sender[:20]}...")
                                return sender
            
        except Exception as e:
            print(f"   [WARN] PolygonScan txlist error: {e}")
        
        return None
    
    def clear_cache(self) -> None:
        """Clear the resolution cache."""
        self._cache.clear()
    
    @property
    def cache_size(self) -> int:
        """Get current cache size."""
        return len(self._cache)
