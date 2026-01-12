"""
Event parser for decoding OrderFilled events from CTF Exchange.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple
from eth_abi import decode
from web3 import Web3

from ..config import USDC_DECIMALS


@dataclass
class ParsedOrderFilled:
    """Parsed OrderFilled event data."""
    tx_hash: str
    block_number: int
    timestamp: datetime
    order_hash: str
    maker: str
    taker: str
    maker_asset_id: int
    taker_asset_id: int
    maker_amount: int  # raw wei
    taker_amount: int  # raw wei
    fee: int
    
    @property
    def is_maker_usdc(self) -> bool:
        """Returns True if maker is providing USDC (asset_id = 0)."""
        return self.maker_asset_id == 0
    
    @property
    def is_taker_usdc(self) -> bool:
        """Returns True if taker is providing USDC (asset_id = 0)."""
        return self.taker_asset_id == 0
    
    @property
    def usdc_amount_raw(self) -> int:
        """Get the USDC amount in raw units (6 decimals)."""
        if self.is_maker_usdc:
            return self.maker_amount
        elif self.is_taker_usdc:
            return self.taker_amount
        return 0
    
    @property
    def usdc_amount(self) -> float:
        """Get the USDC amount as human-readable float."""
        return self.usdc_amount_raw / (10 ** USDC_DECIMALS)
    
    @property
    def outcome_token_id(self) -> str:
        """Get the outcome token asset ID."""
        if self.is_maker_usdc:
            return str(self.taker_asset_id)
        return str(self.maker_asset_id)
    
    @property
    def side(self) -> str:
        """
        Determine trade side from maker's perspective.
        - If maker provides USDC → maker is BUYING outcome tokens
        - If maker provides tokens → maker is SELLING outcome tokens
        """
        return "buy" if self.is_maker_usdc else "sell"


class EventParser:
    """Parser for CTF Exchange events."""
    
    def __init__(self):
        self.w3 = Web3()  # Just for utility functions, no connection needed
    
    def parse_order_filled(
        self,
        log: dict,
        block_timestamp: Optional[datetime] = None
    ) -> Optional[ParsedOrderFilled]:
        """
        Parse an OrderFilled event log.
        
        Args:
            log: Raw event log from web3
            block_timestamp: Optional block timestamp, uses current time if not provided
            
        Returns:
            ParsedOrderFilled object or None if parsing fails
        """
        try:
            # Extract indexed parameters from topics
            # topics[0] = event signature
            # topics[1] = orderHash (bytes32)
            # topics[2] = maker (address, padded to 32 bytes)
            # topics[3] = taker (address, padded to 32 bytes)
            topics = log.get("topics", [])
            if len(topics) < 4:
                return None
            
            order_hash = topics[1].hex() if hasattr(topics[1], 'hex') else topics[1]
            maker = self._extract_address(topics[2])
            taker = self._extract_address(topics[3])
            
            # Decode non-indexed parameters from data
            # makerAssetId, takerAssetId, makerAmountFilled, takerAmountFilled, fee
            data = log.get("data", b"")
            if isinstance(data, str):
                data = bytes.fromhex(data[2:] if data.startswith("0x") else data)
            
            decoded = decode(
                ["uint256", "uint256", "uint256", "uint256", "uint256"],
                data
            )
            maker_asset_id, taker_asset_id, maker_amount, taker_amount, fee = decoded
            
            # Extract transaction and block info
            tx_hash = log.get("transactionHash", b"")
            if hasattr(tx_hash, 'hex'):
                tx_hash = tx_hash.hex()
            elif isinstance(tx_hash, bytes):
                tx_hash = tx_hash.hex()
            
            block_number = log.get("blockNumber", 0)
            if isinstance(block_number, str):
                block_number = int(block_number, 16)
            
            timestamp = block_timestamp or datetime.utcnow()
            
            return ParsedOrderFilled(
                tx_hash=tx_hash,
                block_number=block_number,
                timestamp=timestamp,
                order_hash=order_hash,
                maker=maker,
                taker=taker,
                maker_asset_id=maker_asset_id,
                taker_asset_id=taker_asset_id,
                maker_amount=maker_amount,
                taker_amount=taker_amount,
                fee=fee,
            )
            
        except Exception as e:
            print(f"❌ Failed to parse OrderFilled event: {e}")
            return None
    
    def _extract_address(self, topic: bytes | str) -> str:
        """Extract address from 32-byte padded topic."""
        if isinstance(topic, str):
            # Remove 0x prefix if present, take last 40 chars (20 bytes = address)
            topic = topic[2:] if topic.startswith("0x") else topic
            return Web3.to_checksum_address("0x" + topic[-40:])
        elif hasattr(topic, 'hex'):
            return Web3.to_checksum_address("0x" + topic.hex()[-40:])
        return ""
    
    def format_trade_summary(self, event: ParsedOrderFilled) -> str:
        """Format a human-readable trade summary."""
        side_emoji = "🟢" if event.side == "buy" else "🔴"
        return (
            f"{side_emoji} {event.side.upper()} | "
            f"${event.usdc_amount:,.2f} USDC | "
            f"Maker: {event.maker[:10]}... | "
            f"Block: {event.block_number}"
        )
