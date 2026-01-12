"""
HTTP-based event listener for Polygon OrderFilled events.

Uses HTTP polling instead of WebSocket for compatibility with free-tier RPC providers.
Polls every 2 seconds (matching Polygon block time) for new events.
"""
import asyncio
import aiohttp
from datetime import datetime
from typing import Callable, Optional, Awaitable, Dict, Any

from ..config import (
    POLYGON_HTTP_URL,
    CTF_EXCHANGE_ADDRESS,
    ORDER_FILLED_TOPIC,
    USDC_THRESHOLD,
)
from ..database.repository import Trade, TradeRepository
from .event_parser import EventParser, ParsedOrderFilled


class EventListener:
    """
    HTTP-based listener for CTF Exchange OrderFilled events.
    
    Uses HTTP polling instead of WebSocket for reliability.
    Automatically retries on errors.
    """
    
    POLL_INTERVAL = 2  # seconds (Polygon block time ~2s)
    RECONNECT_DELAY = 5  # seconds on error
    
    def __init__(
        self,
        repository: TradeRepository,
        wss_url: str = "",  # Kept for compatibility but not used
        usdc_threshold: float = USDC_THRESHOLD,
        on_trade: Optional[Callable[[Trade, ParsedOrderFilled], Awaitable[None]]] = None,
    ):
        self.http_url = POLYGON_HTTP_URL
        self.repository = repository
        self.usdc_threshold = usdc_threshold
        self.parser = EventParser()
        self.on_trade = on_trade
        self._running = False
        self._last_block = 0
        self._event_count = 0
        self._last_event_time: Optional[datetime] = None
        
    async def start(self) -> None:
        """Start listening for OrderFilled events via HTTP polling."""
        self._running = True
        
        print(f"[CONNECT] Using HTTP polling mode", flush=True)
        print(f"[TARGET] Monitoring CTF Exchange: {CTF_EXCHANGE_ADDRESS}", flush=True)
        print(f"[CONFIG] USDC Threshold: ${self.usdc_threshold:,.2f}", flush=True)
        print(f"[CONFIG] Poll interval: {self.POLL_INTERVAL}s", flush=True)
        print("-" * 60, flush=True)
        
        while self._running:
            try:
                await self._poll_loop()
            except asyncio.CancelledError:
                print("[STOP] Listener cancelled", flush=True)
                break
            except Exception as e:
                print(f"[ERROR] Polling error: {e}", flush=True)
                if self._running:
                    print(f"[RETRY] Retrying in {self.RECONNECT_DELAY}s...", flush=True)
                    await asyncio.sleep(self.RECONNECT_DELAY)
    
    async def stop(self) -> None:
        """Stop the event listener."""
        self._running = False
        print(f"[STOP] Event listener stopped (processed {self._event_count} events)", flush=True)
    
    async def _poll_loop(self) -> None:
        """Main polling loop using HTTP requests."""
        async with aiohttp.ClientSession() as session:
            # Get initial block number
            self._last_block = await self._get_block_number(session)
            print(f"[OK] Connected at block {self._last_block}", flush=True)
            print(f"[LISTEN] Polling for OrderFilled events...", flush=True)
            
            while self._running:
                current_block = await self._get_block_number(session)
                
                if current_block > self._last_block:
                    # Process new blocks
                    for block_num in range(self._last_block + 1, current_block + 1):
                        await self._process_block(session, block_num)
                    
                    self._last_block = current_block
                
                await asyncio.sleep(self.POLL_INTERVAL)
    
    async def _get_block_number(self, session: aiohttp.ClientSession) -> int:
        """Get current block number via JSON-RPC."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_blockNumber",
            "params": []
        }
        
        async with session.post(self.http_url, json=payload, timeout=10) as resp:
            data = await resp.json()
            if "error" in data:
                raise Exception(f"RPC error: {data['error']}")
            return int(data["result"], 16)
    
    async def _get_block(self, session: aiohttp.ClientSession, block_num: int) -> Dict[str, Any]:
        """Get block data including timestamp."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_getBlockByNumber",
            "params": [hex(block_num), False]
        }
        
        async with session.post(self.http_url, json=payload, timeout=10) as resp:
            data = await resp.json()
            if "error" in data:
                raise Exception(f"RPC error: {data['error']}")
            return data["result"]
    
    async def _get_logs(
        self, 
        session: aiohttp.ClientSession, 
        block_num: int
    ) -> list:
        """Get OrderFilled logs for a specific block."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_getLogs",
            "params": [{
                "address": CTF_EXCHANGE_ADDRESS,
                "topics": [ORDER_FILLED_TOPIC],
                "fromBlock": hex(block_num),
                "toBlock": hex(block_num)
            }]
        }
        
        async with session.post(self.http_url, json=payload, timeout=10) as resp:
            data = await resp.json()
            if "error" in data:
                raise Exception(f"RPC error: {data['error']}")
            return data.get("result", [])
    
    async def _process_block(self, session: aiohttp.ClientSession, block_num: int) -> None:
        """Process a single block for OrderFilled events."""
        try:
            logs = await self._get_logs(session, block_num)
            
            if logs:
                print(f"[BLOCK] Block {block_num}: {len(logs)} OrderFilled events", flush=True)
                
                # Get block timestamp
                block_data = await self._get_block(session, block_num)
                block_timestamp = datetime.utcfromtimestamp(int(block_data["timestamp"], 16))
                
                for log in logs:
                    self._event_count += 1
                    self._last_event_time = datetime.utcnow()
                    await self._process_event(log, block_timestamp)
            
        except Exception as e:
            print(f"[WARN] Error processing block {block_num}: {e}", flush=True)
    
    async def _process_event(self, log: dict, block_timestamp: datetime) -> None:
        """Process a single OrderFilled event."""
        try:
            # Convert hex strings for parser compatibility
            log_formatted = {
                "transactionHash": bytes.fromhex(log["transactionHash"][2:]),
                "blockNumber": int(log["blockNumber"], 16),
                "topics": [bytes.fromhex(t[2:]) for t in log["topics"]],
                "data": bytes.fromhex(log["data"][2:]) if log["data"] != "0x" else b"",
            }
            
            # Parse the event
            parsed = self.parser.parse_order_filled(log_formatted, block_timestamp)
            if not parsed:
                print(f"   [SKIP] Could not parse event", flush=True)
                return
            
            # DEBUG: Always show trade amount
            print(f"   [TRADE] ${parsed.usdc_amount:,.2f} USDC ({parsed.side}) - {parsed.maker[:10]}...", flush=True)
            
            # Check USDC threshold
            if parsed.usdc_amount < self.usdc_threshold:
                print(f"   [SKIP] Below ${self.usdc_threshold:,.2f} threshold", flush=True)
                return
            
            # Log qualifying trade
            print(f"   [QUALIFY] Trade qualifies!", flush=True)
            
            # Create trade record
            trade = Trade(
                tx_hash=parsed.tx_hash,
                block_number=parsed.block_number,
                timestamp=parsed.timestamp,
                order_hash=parsed.order_hash,
                proxy_address=parsed.maker,
                owner_address=None,
                proxy_type=None,
                asset_id=parsed.outcome_token_id,
                side=parsed.side,
                amount_usdc=parsed.usdc_amount,
                market_id=None,
            )
            
            # Insert into database
            trade_id = await self.repository.insert_trade(trade)
            
            if trade_id > 0:
                trade.id = trade_id
                print(f"   [DB] Saved to database (ID: {trade_id})", flush=True)
                
                if self.on_trade:
                    await self.on_trade(trade, parsed)
            else:
                print(f"   [INFO] Duplicate trade, skipped", flush=True)
                
        except Exception as e:
            print(f"[ERROR] Error processing event: {e}", flush=True)
            import traceback
            traceback.print_exc()
    
    async def get_historical_events(
        self,
        from_block: int,
        to_block: int | str = "latest"
    ) -> int:
        """Fetch and process historical OrderFilled events."""
        print(f"[HISTORY] Fetching from block {from_block} to {to_block}...", flush=True)
        
        async with aiohttp.ClientSession() as session:
            if to_block == "latest":
                to_block = await self._get_block_number(session)
            
            count = 0
            for block_num in range(from_block, to_block + 1):
                await self._process_block(session, block_num)
                count += 1
                
                # Small delay to avoid rate limits
                if count % 10 == 0:
                    await asyncio.sleep(0.5)
            
            return count
    
    def get_stats(self) -> dict:
        """Get listener statistics."""
        return {
            "total_events": self._event_count,
            "last_event": self._last_event_time.isoformat() if self._last_event_time else None,
            "last_block": self._last_block,
            "running": self._running,
        }
