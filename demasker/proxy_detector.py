"""
Proxy type detection for Polymarket wallets.
"""
from enum import Enum
from typing import Optional
from web3 import AsyncWeb3


class ProxyType(Enum):
    """Types of Polymarket proxy wallets."""
    GNOSIS_SAFE = "gnosis_safe"
    MAGIC_LINK = "magic_link"  # Polymarket custom proxy
    EOA = "eoa"  # Regular externally owned account
    UNKNOWN = "unknown"


# Known bytecode patterns for proxy detection
# Gnosis Safe Proxy has a specific mastercopy storage pattern
GNOSIS_SAFE_PROXY_BYTECODE_PREFIX = "0x608060405273"

# Polymarket proxy contracts have the "owner()" function selector
OWNER_FUNCTION_SELECTOR = "0x8da5cb5b"


class ProxyDetector:
    """
    Detect the type of Polymarket proxy wallet.
    """
    
    def __init__(self, w3: AsyncWeb3):
        self.w3 = w3
        self._code_cache: dict[str, bytes] = {}
    
    async def detect_type(self, address: str) -> ProxyType:
        """
        Detect the proxy type for a given address.
        
        Args:
            address: The wallet address to check
            
        Returns:
            ProxyType enum value
        """
        try:
            # Get contract bytecode
            code = await self._get_code(address)
            
            # No code = EOA
            if not code or code == b"" or code == b"0x":
                return ProxyType.EOA
            
            code_hex = code.hex() if isinstance(code, bytes) else code
            
            # Check for Gnosis Safe Proxy pattern
            # Safe proxies delegate to a singleton and have specific storage layout
            if await self._is_gnosis_safe(address, code_hex):
                return ProxyType.GNOSIS_SAFE
            
            # Check for Polymarket custom proxy (has owner() function)
            if await self._has_owner_function(address):
                return ProxyType.MAGIC_LINK
            
            return ProxyType.UNKNOWN
            
        except Exception as e:
            print(f"⚠️ Error detecting proxy type for {address}: {e}")
            return ProxyType.UNKNOWN
    
    async def _get_code(self, address: str) -> bytes:
        """Get bytecode with caching."""
        if address not in self._code_cache:
            self._code_cache[address] = await self.w3.eth.get_code(address)
        return self._code_cache[address]
    
    async def _is_gnosis_safe(self, address: str, code_hex: str) -> bool:
        """
        Check if address is a Gnosis Safe proxy.
        Safe proxies have a specific pattern and storage layout.
        """
        # Gnosis Safe Proxy is very small (~60 bytes) and delegates all calls
        # Check for the typical Safe proxy bytecode pattern
        if len(code_hex) < 200:  # Safe proxy is compact
            # Try to read the singleton address from storage slot 0
            try:
                singleton = await self.w3.eth.get_storage_at(address, 0)
                singleton_hex = singleton.hex()
                # If slot 0 has an address-like value, likely a Safe
                if singleton_hex != "0x" + "00" * 32:
                    # Verify by trying to call getOwners
                    return True
            except Exception:
                pass
        
        # Alternative: check for GnosisSafeProxy bytecode pattern
        if "363d3d373d3d3d363d73" in code_hex:  # EIP-1167 minimal proxy pattern
            return True
            
        return False
    
    async def _has_owner_function(self, address: str) -> bool:
        """
        Check if contract has an owner() function.
        This is characteristic of Polymarket custom proxies.
        """
        try:
            # Try calling owner() - if it works, it's a custom proxy
            result = await self.w3.eth.call({
                "to": address,
                "data": OWNER_FUNCTION_SELECTOR,
            })
            return len(result) >= 32
        except Exception:
            return False
