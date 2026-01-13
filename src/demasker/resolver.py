"""
Unified address resolver for Polymarket proxy wallets.
"""
from typing import Optional, Tuple
from web3 import AsyncWeb3

from .proxy_detector import ProxyDetector, ProxyType
from .gnosis_resolver import GnosisResolver
from .magic_resolver import MagicResolver
from .polygonscan_resolver import PolygonScanResolver


class AddressResolver:
    """
    Unified resolver that detects proxy type and resolves owner EOA.
    Includes caching to reduce RPC calls.
    
    Resolution order:
    1. Gnosis Safe (getOwners)
    2. Magic Link (owner())
    3. PolygonScan API (contract creator) - NEW fallback
    """
    
    def __init__(self, w3: AsyncWeb3):
        self.w3 = w3
        self.detector = ProxyDetector(w3)
        self.gnosis_resolver = GnosisResolver(w3)
        self.magic_resolver = MagicResolver(w3)
        self.polygonscan_resolver = PolygonScanResolver()  # NEW: API fallback
        
        # Cache resolved addresses: proxy_address -> (owner_address, proxy_type)
        self._cache: dict[str, Tuple[Optional[str], ProxyType]] = {}
    
    
    # Known Factory/Infrastructure addresses to ignore
    BLACKLISTED_OWNERS = {
        "0xe672e75b6e824cda8e66c4b0bfce1a1efb6be1165a",  # Gnosis Safe Proxy Factory 1.3.0
        "0xa6b71e26c5e0845f74c812102ca7114b6a896ab2",  # Gnosis Safe Proxy Factory 1.3.0
        "0x09583cb666c9a687adeafb50fd556adb6bae6fa6",  # Seen in logs as factory
    }

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
        
        # NEW: If on-chain resolution failed, try PolygonScan API
        if not owner_address and proxy_type != ProxyType.EOA:
            print(f"   [FALLBACK] Trying PolygonScan API for {proxy_address[:20]}...")
            
            # First try: Get contract creator
            creator = await self.polygonscan_resolver.get_creator(proxy_address)
            
            if creator:
                # Convert to checksum address (PolygonScan returns lowercase)
                creator = self.w3.to_checksum_address(creator)
                
                # Check blacklist immediately
                if creator.lower() in self.BLACKLISTED_OWNERS:
                     # Creator is a known factory, skip using it as owner
                     # Try to get first funder instead
                    pass
                else:
                    # Check if creator is an EOA (not a factory)
                    creator_code = await self.w3.eth.get_code(creator)
                    if not creator_code or creator_code == b"":
                        # Creator is an EOA, use it
                        owner_address = creator
                
                # If still no owner (because creator was factory/blacklisted), try first funder
                if not owner_address:
                    # Creator is a contract (factory), try to get first funder
                    funder = await self.polygonscan_resolver.get_first_funder(proxy_address)
                    if funder:
                        # Convert to checksum
                        funder_checksum = self.w3.to_checksum_address(funder)
                        
                        # Check blacklist for funder too
                        if funder_checksum.lower() not in self.BLACKLISTED_OWNERS:
                            owner_address = funder_checksum
        
        # Final check: If owner found is blacklisted (e.g. resolved on-chain), discard it
        if owner_address and owner_address.lower() in self.BLACKLISTED_OWNERS:
            print(f"   [WARN] Resolved owner {owner_address[:10]}... is BLACKLISTED (Factory). Ignoring.")
            owner_address = None

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
        self.polygonscan_resolver.clear_cache()
    
    @property
    def cache_size(self) -> int:
        """Get current cache size."""
        return len(self._cache)

