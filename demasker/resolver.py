"""
Unified address resolver for Polymarket proxy wallets.
"""
from typing import Optional, Tuple
from web3 import AsyncWeb3

from .proxy_detector import ProxyDetector, ProxyType
from .gnosis_resolver import GnosisResolver
from .magic_resolver import MagicResolver


class AddressResolver:
    """
    Unified resolver that detects proxy type and resolves owner EOA.
    Includes caching to reduce RPC calls.
    """
    
    def __init__(self, w3: AsyncWeb3):
        self.w3 = w3
        self.detector = ProxyDetector(w3)
        self.gnosis_resolver = GnosisResolver(w3)
        self.magic_resolver = MagicResolver(w3)
        
        # Cache resolved addresses: proxy_address -> (owner_address, proxy_type)
        self._cache: dict[str, Tuple[Optional[str], ProxyType]] = {}
    
    async def resolve(self, proxy_address: str) -> Tuple[Optional[str], ProxyType]:
        """
        Resolve a proxy address to its owner EOA.
        
        Args:
            proxy_address: The proxy wallet address
            
        Returns:
            Tuple of (owner_address, proxy_type)
            owner_address is None if resolution fails or address is already an EOA
        """
        # Check cache first
        if proxy_address in self._cache:
            return self._cache[proxy_address]
        
        # Detect proxy type
        proxy_type = await self.detector.detect_type(proxy_address)
        
        owner_address: Optional[str] = None
        
        if proxy_type == ProxyType.EOA:
            # Already an EOA, owner is the address itself
            owner_address = proxy_address
            
        elif proxy_type == ProxyType.GNOSIS_SAFE:
            # Resolve via Gnosis Safe
            owner_address = await self.gnosis_resolver.get_owner(proxy_address)
            
        elif proxy_type == ProxyType.MAGIC_LINK:
            # Resolve via Magic Link proxy
            owner_address = await self.magic_resolver.get_owner(proxy_address)
            
        else:
            # Unknown proxy type - try both resolvers
            owner_address = await self.gnosis_resolver.get_owner(proxy_address)
            if not owner_address:
                owner_address = await self.magic_resolver.get_owner(proxy_address)
        
        # Cache the result
        self._cache[proxy_address] = (owner_address, proxy_type)
        
        return owner_address, proxy_type
    
    async def resolve_batch(
        self,
        addresses: list[str]
    ) -> dict[str, Tuple[Optional[str], ProxyType]]:
        """
        Resolve multiple addresses.
        
        Args:
            addresses: List of proxy addresses to resolve
            
        Returns:
            Dict mapping proxy_address -> (owner_address, proxy_type)
        """
        results = {}
        for address in addresses:
            results[address] = await self.resolve(address)
        return results
    
    def clear_cache(self) -> None:
        """Clear the resolution cache."""
        self._cache.clear()
    
    @property
    def cache_size(self) -> int:
        """Get current cache size."""
        return len(self._cache)
