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
    POLYGONSCAN_API_KEY,
    POLYGONSCAN_BASE_URL,
    POLYGON_CHAIN_ID,
    CTF_EXCHANGE_ADDRESS,
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


@dataclass
class ConvictionAlert:
    """Alert data when conviction is confirmed (held > 20 mins)."""
    wallet_address: str
    asset_id: str
    initial_shares: float
    current_shares: float
    trade_amount_usdc: float
    minutes_held: int
    tx_hash: str


class ExecutionGuard:
    """
    Monitors whale positions for dump activity after initial trade.
    
    - If a wallet sells >20% of their position within 60 minutes of buying, sends MANIPULATION WARNING.
    - If a wallet holds usage > 20 minutes without selling, sends CONVICTION CONFIRMED.
    """
    
    def __init__(
        self,
        repository,
        on_dump_detected: Optional[Callable[[DumpAlert], Awaitable[None]]] = None,
        on_conviction_confirmed: Optional[Callable[[ConvictionAlert], Awaitable[None]]] = None,
    ):
        self.repository = repository
        self.monitored_positions: Dict[str, MonitoredPosition] = {}
        self.on_dump_detected = on_dump_detected
        self.on_conviction_confirmed = on_conviction_confirmed
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
        """
        key = f"{wallet_address}:{asset_id}"
        now = datetime.utcnow()
        
        # Check if already monitoring (accumulation) - extend duration?
        # For simple implementation, we overwrite or keep existing start time?
        # If we overwrite, we reset the conviction timer.
        # If we keep, we might alert too soon (based on first trade).
        # User says "held its $3k+ position".
        # Let's keep existing start time if exists to reward accumulation.
        if key in self.monitored_positions:
            # Update shares and amount, keep start time
            existing = self.monitored_positions[key]
            existing.initial_shares += shares
            existing.trade_amount_usdc += trade_amount_usdc
            existing.last_checked = now # Reset checked
            print(f"[GUARD] Updating position {wallet_address[:10]}... total shares: {existing.initial_shares:,.0f}")
            return

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
        """Check all monitored positions for dumps and conviction."""
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
                    
                    # Calculate minutes since start
                    minutes_after = int((now - position.monitor_start).total_seconds() / 60)

                    # 1. CHECK FOR DUMP
                    if position.initial_shares > 0:
                        sold_shares = position.initial_shares - current_shares
                        
                        # Only flag as dump if sold positive amount (balance decreased)
                        if sold_shares > 0:
                            dump_percent = sold_shares / position.initial_shares
                            
                            if dump_percent >= self.dump_threshold:
                                position.dumped = True
                                position.dump_percent = dump_percent
                                
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
                                continue

                    # 2. CHECK FOR CONVICTION (True Whale)
                    # "Held position for more than 20 minutes without selling a single share"
                    # "Only trigger ... if the wallet has held its $3,000+ position"
                    if (minutes_after >= 20 and 
                        not getattr(position, 'conviction_sent', False) and 
                        not position.dumped and
                        position.trade_amount_usdc >= 3000):
                        
                        # Ensure NO selling (current_shares >= initial_shares)
                        # We allow >= because they might have accumulated more (buy)
                        if current_shares >= position.initial_shares * 0.99: # Allow 1% wiggle room for rounding
                            
                            print(f"[ALERT] CONVICTION CONFIRMED: {position.wallet_address[:10]}... held > 20m")
                            
                            # Mark as sent
                            position.conviction_sent = True
                            
                            conviction = ConvictionAlert(
                                wallet_address=position.wallet_address,
                                asset_id=position.asset_id,
                                initial_shares=position.initial_shares,
                                current_shares=current_shares,
                                trade_amount_usdc=position.trade_amount_usdc,
                                minutes_held=minutes_after,
                                tx_hash=position.tx_hash,
                            )
                            
                            if self.on_conviction_confirmed:
                                await self.on_conviction_confirmed(conviction)
                                            
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
        
        Uses PolygonScan API to get ERC-1155 transfers and calculate balance.
        """
        if not POLYGONSCAN_API_KEY:
            return None
        
        params = {
            "chainid": POLYGON_CHAIN_ID,
            "module": "account",
            "action": "token1155tx",
            "address": wallet_address,
            "contractaddress": CTF_EXCHANGE_ADDRESS,
            "page": 1,
            "offset": 100,
            "sort": "desc",
            "apikey": POLYGONSCAN_API_KEY,
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(POLYGONSCAN_BASE_URL, params=params, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        
                        if data.get("status") != "1" or not data.get("result"):
                            return 0.0
                        
                        # Calculate net balance for the specific token
                        balance = 0.0
                        for tx in data["result"]:
                            if tx.get("tokenID") == asset_id:
                                value = float(tx.get("tokenValue", 0))
                                if tx.get("to", "").lower() == wallet_address.lower():
                                    balance += value
                                elif tx.get("from", "").lower() == wallet_address.lower():
                                    balance -= value
                        
                        return max(0.0, balance)
                    else:
                        return None
        except Exception as e:
            print(f"[ERROR] PolygonScan NFT query failed: {e}")
            return None
    
    @property
    def active_monitors(self) -> int:
        """Number of positions currently being monitored."""
        return len(self.monitored_positions)

    async def validate_alert(self, wallet_address: str, asset_id: str) -> bool:
        """
        Validate if an alert should be sent.
        Returns False if the wallet has a history of high-frequency flipping (<30 mins).
        known as the "Maturity Filter".
        """
        is_flipper = await self.repository.check_flipping_activity(
            wallet_address, 
            asset_id, 
            minutes=30
        )
        if is_flipper:
             print(f"[GUARD] Silent Discard: {wallet_address[:10]}... detected as High-Frequency Flipper")
             return False # Discard
        return True

    async def check_conviction(self, wallet_address: str, asset_id: str) -> bool:
        """
        Check if the position qualifies for a 'True Whale' Conviction Alert.
        Requires holding the position for > 20 minutes (accumulation verification).
        """
        hold_minutes = await self.repository.get_position_hold_time(wallet_address, asset_id)
        if hold_minutes > 20:
            return True
        return False
