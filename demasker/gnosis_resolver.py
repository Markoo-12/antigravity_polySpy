"""
Gnosis Safe owner resolution.
"""
from typing import Optional
from web3 import AsyncWeb3
from web3.exceptions import ContractLogicError

from ..config import GNOSIS_SAFE_ABI


class GnosisResolver:
    """
    Resolve the owner EOA from a Gnosis Safe proxy wallet.
    Polymarket uses 1-of-1 multisig Safes where the user's EOA is the sole owner.
    """
    
    def __init__(self, w3: AsyncWeb3):
        self.w3 = w3
    
    async def get_owner(self, safe_address: str) -> Optional[str]:
        """
        Get the owner EOA from a Gnosis Safe.
        
        Args:
            safe_address: The Gnosis Safe proxy address
            
        Returns:
            The owner EOA address, or None if resolution fails
        """
        try:
            # Create contract instance
            safe_contract = self.w3.eth.contract(
                address=self.w3.to_checksum_address(safe_address),
                abi=GNOSIS_SAFE_ABI,
            )
            
            # Call getOwners() - returns array of owner addresses
            owners = await safe_contract.functions.getOwners().call()
            
            if owners and len(owners) > 0:
                # Polymarket uses 1-of-1 multisig, so first owner is the EOA
                return self.w3.to_checksum_address(owners[0])
            
            return None
            
        except ContractLogicError as e:
            print(f"⚠️ Contract error resolving Safe owner for {safe_address}: {e}")
            return None
        except Exception as e:
            print(f"⚠️ Error resolving Safe owner for {safe_address}: {e}")
            return None
