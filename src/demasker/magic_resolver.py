"""
Magic Link / Polymarket custom proxy owner resolution.
"""
from typing import Optional
from web3 import AsyncWeb3
from web3.exceptions import ContractLogicError

from ..config import POLYMARKET_PROXY_ABI


class MagicResolver:
    """
    Resolve the owner EOA from a Polymarket Magic Link proxy wallet.
    These custom proxies have an owner() function that returns the controlling EOA.
    """
    
    def __init__(self, w3: AsyncWeb3):
        self.w3 = w3
    
    async def get_owner(self, proxy_address: str) -> Optional[str]:
        """
        Get the owner EOA from a Magic Link proxy.
        
        Args:
            proxy_address: The Magic Link proxy address
            
        Returns:
            The owner EOA address, or None if resolution fails
        """
        try:
            # Create contract instance with minimal ABI
            proxy_contract = self.w3.eth.contract(
                address=self.w3.to_checksum_address(proxy_address),
                abi=POLYMARKET_PROXY_ABI,
            )
            
            # Call owner() function
            owner = await proxy_contract.functions.owner().call()
            
            if owner and owner != "0x" + "00" * 20:
                return self.w3.to_checksum_address(owner)
            
            return None
            
        except ContractLogicError as e:
            # Fallback: try reading from storage slot
            return await self._read_owner_from_storage(proxy_address)
        except Exception as e:
            print(f"[WARN] Error resolving Magic proxy owner for {proxy_address}: {e}")
            return await self._read_owner_from_storage(proxy_address)
    
    async def _read_owner_from_storage(self, proxy_address: str) -> Optional[str]:
        """
        Fallback: read owner from storage slot.
        Some proxy implementations store owner in slot 0.
        """
        try:
            # Common storage slots for owner
            for slot in [0, 1]:
                storage = await self.w3.eth.get_storage_at(proxy_address, slot)
                
                # Check if this looks like an address (last 20 bytes)
                if storage and len(storage) >= 20:
                    # Extract potential address from packed storage
                    potential_address = "0x" + storage.hex()[-40:]
                    
                    # Validate it's not zero address
                    if potential_address != "0x" + "00" * 20:
                        # Check if this address has no code (is an EOA)
                        code = await self.w3.eth.get_code(potential_address)
                        if not code or code == b"":
                            return self.w3.to_checksum_address(potential_address)
            
            return None
            
        except Exception as e:
            print(f"[WARN] Error reading storage for {proxy_address}: {e}")
            return None
