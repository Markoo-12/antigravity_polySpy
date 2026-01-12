"""
Execution Guard - Post-trade monitoring for wash/dump detection.

Monitors whale wallets for 60 minutes after initial trade to detect
if they dump their position (indicating pump-and-dump manipulation).
"""
import asyncio
import aiohttp
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Callable, Awaitable
import json

from ..config import (
    MONITOR_DURATION_MINUTES,
    DUMP_THRESHOLD_PERCENT,
    MONITOR_CHECK_INTERVAL,
    MORALIS_API_KEY,
)


@dataclass
class MonitoredPosition:
    """A position being monitored for dump activity."""
    wallet_address: str
    asset_id: str
    initial_shares: float
    trade_amount_usdc: float
    monitor_start: datetime
    monitor_end: datetime
    tx_hash: str
    last_checked: Optional[datetime] = None
    current_shares: Optional[float] = None
    dumped: bool = False
    dump_percent: float = 0.0


@dataclass
class DumpAlert:
    """Alert data when a dump is detected."""
    wallet_address: str
    asset_id: str
    initial_shares: float
    sold_shares: float
    dump_percent: float
    minutes_after_buy: int
    tx_hash: str


class ExecutionGuard:
    """
    Monitors whale positions for dump activity after initial trade.
    
    If a wallet sells >20% of their position within 60 minutes of buying,
    sends a MANIPULATION WARNING alert.
    """
    
    def __init__(
        self,
        on_dump_detected: Optional[Callable[[DumpAlert], Awaitable[None]]] = None,
    ):
        self.monitored_positions: Dict[str, MonitoredPosition] = {}
        self.on_dump_detected = on_dump_detected
        self._running = False
        self._monitor_task: Optional[asyncio.Task] = None
        
        self.monitor_duration = timedelta(minutes=MONITOR_DURATION_MINUTES)
        self.dump_threshold = DUMP_THRESHOLD_PERCENT
        self.check_interval = MONITOR_CHECK_INTERVAL
    
    async def start(self) -> None:
        """Start the background monitoring loop."""
        if self._running:
            return
        
        self._running = True
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        print("[GUARD] Execution Guard started - monitoring for dumps")
    
    async def stop(self) -> None:
        """Stop the background monitoring loop."""
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        print("[GUARD] Execution Guard stopped")
    
    def add_position(
        self,
        wallet_address: str,
        asset_id: str,
        shares: float,
        trade_amount_usdc: float,
        tx_hash: str,
    ) -> None:
        """
        Add a position to monitor.
        
        Args:
            wallet_address: The whale's wallet address
            asset_id: The outcome token ID
            shares: Number of shares bought
            trade_amount_usdc: Trade size in USDC
            tx_hash: Transaction hash of the buy
        """
        key = f"{wallet_address}:{asset_id}"
        now = datetime.utcnow()
        
        self.monitored_positions[key] = MonitoredPosition(
            wallet_address=wallet_address,
            asset_id=asset_id,
            initial_shares=shares,
            trade_amount_usdc=trade_amount_usdc,
            monitor_start=now,
            monitor_end=now + self.monitor_duration,
            tx_hash=tx_hash,
        )
        
        print(f"[GUARD] Monitoring {wallet_address[:10]}... for {MONITOR_DURATION_MINUTES} minutes")
    
    async def _monitor_loop(self) -> None:
        """Background loop that checks monitored positions."""
        while self._running:
            try:
                await self._check_all_positions()
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[ERROR] Execution Guard error: {e}")
                await asyncio.sleep(30)
    
    async def _check_all_positions(self) -> None:
        """Check all monitored positions for dumps."""
        now = datetime.utcnow()
        expired_keys = []
        
        for key, position in list(self.monitored_positions.items()):
            # Check if monitoring period expired
            if now >= position.monitor_end:
                expired_keys.append(key)
                continue
            
            # Check current position
            try:
                current_shares = await self._get_current_shares(
                    position.wallet_address,
                    position.asset_id,
                )
                
                if current_shares is not None:
                    position.current_shares = current_shares
                    position.last_checked = now
                    
                    # Calculate dump percentage
                    if position.initial_shares > 0:
                        sold_shares = position.initial_shares - current_shares
                        dump_percent = sold_shares / position.initial_shares
                        
                        if dump_percent >= self.dump_threshold:
                            position.dumped = True
                            position.dump_percent = dump_percent
                            
                            # Calculate minutes since buy
                            minutes_after = int((now - position.monitor_start).total_seconds() / 60)
                            
                            alert = DumpAlert(
                                wallet_address=position.wallet_address,
                                asset_id=position.asset_id,
                                initial_shares=position.initial_shares,
                                sold_shares=sold_shares,
                                dump_percent=dump_percent,
                                minutes_after_buy=minutes_after,
                                tx_hash=position.tx_hash,
                            )
                            
                            print(f"[ALERT] DUMP DETECTED: {position.wallet_address[:10]}... sold {dump_percent:.0%}")
                            
                            if self.on_dump_detected:
                                await self.on_dump_detected(alert)
                            
                            # Stop monitoring this position
                            expired_keys.append(key)
                            
            except Exception as e:
                print(f"[WARN] Failed to check position {key}: {e}")
        
        # Remove expired/completed positions
        for key in expired_keys:
            del self.monitored_positions[key]
    
    async def _get_current_shares(
        self,
        wallet_address: str,
        asset_id: str,
    ) -> Optional[float]:
        """
        Get current share balance for a wallet/asset.
        
        Uses Moralis NFT API since Polymarket positions are ERC1155 tokens.
        """
        if not MORALIS_API_KEY:
            return None
        
        # Polymarket CTF contract
        ctf_contract = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
        
        url = f"https://deep-index.moralis.io/api/v2.2/{wallet_address}/nft"
        params = {
            "chain": "polygon",
            "token_addresses": [ctf_contract],
        }
        headers = {
            "X-API-Key": MORALIS_API_KEY,
            "accept": "application/json",
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, headers=headers, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        
                        # Find the specific token
                        for nft in data.get("result", []):
                            if nft.get("token_id") == asset_id:
                                return float(nft.get("amount", 0))
                        
                        # Not found = 0 shares
                        return 0.0
                    else:
                        return None
        except Exception as e:
            print(f"[ERROR] Moralis NFT query failed: {e}")
            return None
    
    @property
    def active_monitors(self) -> int:
        """Number of positions currently being monitored."""
        return len(self.monitored_positions)
