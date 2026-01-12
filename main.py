"""
Polymarket Insider Sentinel - Main Entry Point
Monitors high-conviction trades, resolves owner addresses, 
calculates insider scores, and sends Telegram alerts.

Phase 5: Profit-Logic Layer Integration
- Upside Validator (price ceiling, slippage, alpha gap)
- Late-Stage Sentinel (+60 for mature stagnant markets)
- Execution Guard (dump monitoring)
- Cluster Detector (multi-wallet coordination)
"""
import asyncio
import signal
import sys
from datetime import datetime
from typing import Optional

from web3 import AsyncWeb3
from web3.providers import WebSocketProvider, AsyncHTTPProvider


def create_web3_provider(url: str):
    """Create appropriate Web3 provider based on URL protocol."""
    if url.startswith(('ws://', 'wss://')):
        return WebSocketProvider(url)
    else:
        return AsyncHTTPProvider(url)

from src.config import (
    POLYGON_WSS_URL, 
    DATABASE_PATH, 
    USDC_THRESHOLD,
    FORENSIC_USDC_THRESHOLD,
    INSIDER_ALERT_THRESHOLD,
    DATA_RETENTION_DAYS,
)
from src.database import init_database, TradeRepository
from src.streamer import EventListener
from src.streamer.event_parser import ParsedOrderFilled
from src.demasker import AddressResolver
from src.forensic import InsiderScorer, ClusterDetector, LateStageSentinel
from src.execution import UpsideValidator, ExecutionGuard
from src.alerts import TelegramAlertBot
from src.alerts.telegram_bot import AlertData, ClusterAlertData, DumpAlertData
from src.database.repository import Trade


class InsiderSentinel:
    """
    Main orchestrator for the Polymarket surveillance system.
    Combines all phases:
    - Phase 1: The Streamer (WebSocket listener)
    - Phase 2: The De-Masker (Proxy resolution)
    - Phase 3: The Forensic Auditor (Insider scoring)
    - Phase 4: The Alert System (Telegram alerts)
    - Phase 5: Profit-Logic Layer (Upside validation, dump guard, clusters)
    """
    
    def __init__(
        self,
        wss_url: str = POLYGON_WSS_URL,
        db_path: str = DATABASE_PATH,
        usdc_threshold: float = USDC_THRESHOLD,
    ):
        self.wss_url = wss_url
        self.db_path = db_path
        self.usdc_threshold = usdc_threshold
        
        # Core components
        self.repository: Optional[TradeRepository] = None
        self.listener: Optional[EventListener] = None
        self.resolver: Optional[AddressResolver] = None
        self.scorer: Optional[InsiderScorer] = None
        self.telegram: Optional[TelegramAlertBot] = None
        self._w3: Optional[AsyncWeb3] = None
        self._shutdown = False
        
        # Phase 5: Profit-Logic Layer components
        self.upside_validator: Optional[UpsideValidator] = None
        self.late_stage_sentinel: Optional[LateStageSentinel] = None
        self.execution_guard: Optional[ExecutionGuard] = None
        self.cluster_detector: Optional[ClusterDetector] = None
    
    async def start(self) -> None:
        """Start the surveillance system."""
        print("=" * 60)
        print("[SEARCH] POLYMARKET INSIDER SENTINEL v2.0")
        print("=" * 60)
        print(f"Started at: {datetime.now().isoformat()}")
        print(f"Database: {self.db_path}")
        print(f"USDC Threshold (Stream): ${self.usdc_threshold:,.2f}")
        print(f"USDC Threshold (Forensic): ${FORENSIC_USDC_THRESHOLD:,.2f}")
        print(f"Alert Threshold: {INSIDER_ALERT_THRESHOLD}/100")
        print("-" * 60)
        print("[LAYER] Profit-Logic Layer: ACTIVE")
        print("  - Upside Validator (price ceiling, slippage)")
        print("  - Late-Stage Sentinel (+60 bonus)")
        print("  - Execution Guard (dump monitoring)")
        print("  - Cluster Detector (multi-wallet)")
        print("=" * 60)
        
        # Initialize database
        await init_database(self.db_path)
        self.repository = TradeRepository(self.db_path)
        
        # Create Web3 connection for resolver
        self._w3 = AsyncWeb3(create_web3_provider(self.wss_url))
        self.resolver = AddressResolver(self._w3)
        
        # Initialize forensic scorer
        self.scorer = InsiderScorer()
        
        # Initialize Phase 5 components
        self.upside_validator = UpsideValidator()
        self.late_stage_sentinel = LateStageSentinel()
        self.cluster_detector = ClusterDetector()
        
        # Initialize Telegram bot
        self.telegram = TelegramAlertBot()
        
        # Initialize Execution Guard with dump callback
        self.execution_guard = ExecutionGuard(
            on_dump_detected=self._on_dump_detected,
        )
        
        if self.telegram.is_configured():
            print("[TELEGRAM] Telegram alerts enabled")
        else:
            print("[WARN] Telegram not configured (set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)")
        
        # Start Execution Guard background task
        await self.execution_guard.start()
        
        # Create event listener with callback
        self.listener = EventListener(
            repository=self.repository,
            wss_url=self.wss_url,
            usdc_threshold=self.usdc_threshold,
            on_trade=self._on_new_trade,
        )
        
        # Start listening
        await self.listener.start()
    
    async def _on_new_trade(self, trade: Trade, parsed: ParsedOrderFilled) -> None:
        """
        Callback when a new trade is detected.
        Resolves owner, calculates insider score, validates upside, and sends alerts.
        """
        if not trade.id or not self.resolver or not self.repository:
            return
        
        owner_address = None
        proxy_type_value = None
        
        try:
            # Phase 2: Resolve proxy to owner EOA
            owner_address, proxy_type = await self.resolver.resolve(trade.proxy_address)
            
            if owner_address:
                await self.repository.update_owner(
                    trade.id,
                    owner_address,
                    proxy_type.value,
                )
                proxy_type_value = proxy_type.value
                print(f"   [USER] Owner: {owner_address[:10]}... ({proxy_type.value})")
            else:
                # Fallback: use proxy address as owner (still valuable for alerts)
                owner_address = trade.proxy_address
                proxy_type_value = "unknown"
                print(f"   [WARN] Could not resolve owner, using proxy: {trade.proxy_address[:10]}...")
                
        except Exception as e:
            # Fallback on error: use proxy address and continue
            owner_address = trade.proxy_address
            proxy_type_value = "error"
            print(f"   [WARN] Error resolving owner ({e}), using proxy address")
        
        # Phase 3, 4 & 5: Forensic analysis with Profit-Logic Layer
        if trade.amount_usdc >= FORENSIC_USDC_THRESHOLD and owner_address and self.scorer:
            try:
                print(f"   [ANALYSIS] Running forensic analysis...")
                
                # Calculate base insider score
                score_result = await self.scorer.calculate_score(
                    owner_address=owner_address,
                    trade_timestamp=trade.timestamp,
                    trade_amount_usdc=trade.amount_usdc,
                    asset_id=trade.asset_id,
                )
                
                total_score = score_result.score
                reasons = list(score_result.reasons)
                
                # Phase 5.2: Late-Stage Sentinel bonus
                if self.late_stage_sentinel:
                    late_stage_result = await self.late_stage_sentinel.analyze(
                        asset_id=trade.asset_id,
                        trade_amount_usdc=trade.amount_usdc,
                    )
                    
                    if late_stage_result.is_late_stage:
                        total_score += late_stage_result.score_bonus
                        reasons.append(f"Late-stage pattern: {late_stage_result.reason} [+{late_stage_result.score_bonus}]")
                        print(f"   [LATE-STAGE] +{late_stage_result.score_bonus} points: {late_stage_result.reason}")
                
                # Phase 5.1: Upside Validation
                upside_valid = True
                current_price = None
                
                if self.upside_validator:
                    upside_result = await self.upside_validator.validate(
                        asset_id=trade.asset_id,
                        insider_entry_price=None,  # We don't know their exact entry
                        side=trade.side,
                    )
                    
                    upside_valid = upside_result.is_valid
                    current_price = upside_result.current_price
                    
                    # Apply slippage penalty
                    if upside_result.score_adjustment != 0:
                        total_score += upside_result.score_adjustment
                        reasons.append(f"Slippage penalty: {upside_result.slippage_percent:.1%} slippage [{upside_result.score_adjustment}]")
                    
                    if not upside_valid:
                        print(f"   [FILTER] Trade filtered by Upside Validator: {upside_result.rejection_reasons}")
                
                # Store score in database (even if filtered)
                bridge_funded = False
                bridge_name = None
                win_rate = None
                
                if score_result.bridge_result:
                    bridge_funded = score_result.bridge_result.is_bridge_funded
                    bridge_name = score_result.bridge_result.bridge_name
                
                if score_result.win_rate_result:
                    win_rate = score_result.win_rate_result.win_rate
                
                await self.repository.update_insider_score(
                    trade_id=trade.id,
                    insider_score=total_score,
                    score_reasons=score_result.to_json(),
                    bridge_funded=bridge_funded,
                    bridge_name=bridge_name,
                    win_rate=win_rate,
                )
                
                # Print score summary
                is_alert_worthy = total_score >= INSIDER_ALERT_THRESHOLD
                icon = "[ALERT]" if is_alert_worthy else "[SCORE]"
                print(f"   {icon} Insider Score: {total_score}/100+")
                for reason in reasons:
                    print(f"      - {reason}")
                
                # Phase 5.4: Add to Cluster Detector
                if self.cluster_detector and is_alert_worthy:
                    cluster_alert = self.cluster_detector.add_trade(
                        wallet_address=owner_address,
                        asset_id=trade.asset_id,
                        insider_score=total_score,
                        trade_amount_usdc=trade.amount_usdc,
                        side=trade.side,
                        tx_hash=trade.tx_hash,
                        timestamp=trade.timestamp,
                    )
                    
                    if cluster_alert and self.telegram and self.telegram.is_configured():
                        # Send CONVICTION CLUSTER alert
                        cluster_data = ClusterAlertData(
                            asset_id=cluster_alert.asset_id,
                            wallets=cluster_alert.wallets,
                            total_amount_usdc=cluster_alert.total_amount_usdc,
                            avg_score=cluster_alert.avg_score,
                            time_span_seconds=cluster_alert.time_span_seconds,
                            market_slug=trade.market_id,
                            outcome="Yes" if trade.side == "buy" else "No",
                        )
                        await self.telegram.send_cluster_alert(cluster_data)
                
                # Phase 4: Send Telegram alert (if passes upside validation)
                if is_alert_worthy and upside_valid and self.telegram and self.telegram.is_configured():
                    alert_data = AlertData(
                        insider_score=total_score,
                        trade_amount_usdc=trade.amount_usdc,
                        side=trade.side,
                        asset_id=trade.asset_id,
                        owner_address=owner_address,
                        proxy_address=trade.proxy_address,
                        tx_hash=trade.tx_hash,
                        reasons=reasons,
                        market_id=trade.market_id,
                        market_slug=trade.market_id,  # Will be resolved to slug if available
                        outcome="Yes" if trade.side == "buy" else "No",
                        current_price=current_price,
                    )
                    await self.telegram.send_alert(alert_data)
                    
                    # Phase 5.3: Start Execution Guard monitoring
                    if self.execution_guard and trade.side == "buy":
                        # Estimate shares from USDC amount and price
                        estimated_shares = trade.amount_usdc / (current_price or 0.5)
                        
                        self.execution_guard.add_position(
                            wallet_address=owner_address,
                            asset_id=trade.asset_id,
                            shares=estimated_shares,
                            trade_amount_usdc=trade.amount_usdc,
                            tx_hash=trade.tx_hash,
                        )
                
                elif is_alert_worthy and not upside_valid:
                    print(f"   [SKIP] Alert suppressed - failed upside validation")
                    
            except Exception as e:
                print(f"   [ERROR] Error in forensic analysis: {e}")
    
    async def _on_dump_detected(self, dump_alert) -> None:
        """Callback when Execution Guard detects a dump."""
        if self.telegram and self.telegram.is_configured():
            dump_data = DumpAlertData(
                wallet_address=dump_alert.wallet_address,
                asset_id=dump_alert.asset_id,
                initial_shares=dump_alert.initial_shares,
                sold_shares=dump_alert.sold_shares,
                dump_percent=dump_alert.dump_percent,
                minutes_after_buy=dump_alert.minutes_after_buy,
                tx_hash=dump_alert.tx_hash,
            )
            await self.telegram.send_dump_warning(dump_data)
    
    async def stop(self) -> None:
        """Stop the surveillance system gracefully."""
        self._shutdown = True
        
        if self.listener:
            await self.listener.stop()
        
        if self.execution_guard:
            await self.execution_guard.stop()
        
        # Print summary
        if self.repository:
            count = await self.repository.get_trade_count()
            print(f"\n[STATS] Total trades logged: {count}")
            
            high_score = await self.repository.get_high_score_trades(limit=5)
            if high_score:
                print(f"[ALERT] High-score trades: {len(high_score)}")
        
        if self.resolver:
            print(f"[CACHE] Resolver cache size: {self.resolver.cache_size}")
        
        if self.cluster_detector:
            stats = self.cluster_detector.get_stats()
            print(f"[CLUSTER] Assets tracked: {stats['assets_tracked']}, Clusters alerted: {stats['clusters_alerted']}")
        
        if self.execution_guard:
            print(f"[GUARD] Active monitors: {self.execution_guard.active_monitors}")
        
        print("\n[OK] Shutdown complete")
    
    async def backfill(self, from_block: int, to_block: int | str = "latest") -> None:
        """
        Backfill historical trades.
        
        Args:
            from_block: Starting block number
            to_block: Ending block number or 'latest'
        """
        print("[HISTORY] Starting historical backfill...")
        
        await init_database(self.db_path)
        self.repository = TradeRepository(self.db_path)
        
        self._w3 = AsyncWeb3(create_web3_provider(self.wss_url))
        self.resolver = AddressResolver(self._w3)
        self.scorer = InsiderScorer()
        self.telegram = TelegramAlertBot()
        
        # Initialize Phase 5 components for backfill
        self.upside_validator = UpsideValidator()
        self.late_stage_sentinel = LateStageSentinel()
        self.cluster_detector = ClusterDetector()
        
        listener = EventListener(
            repository=self.repository,
            wss_url=self.wss_url,
            usdc_threshold=self.usdc_threshold,
            on_trade=self._on_new_trade,
        )
        
        count = await listener.get_historical_events(from_block, to_block)
        print(f"[OK] Backfill complete: {count} events processed")
    
    async def test_telegram(self) -> None:
        """Test Telegram bot configuration."""
        print("[TEST] Testing Telegram bot...")
        
        self.telegram = TelegramAlertBot()
        
        if not self.telegram.bot_token:
            print("[ERROR] TELEGRAM_BOT_TOKEN not set in .env")
            return
        
        if not self.telegram.chat_id:
            print("[WARN] TELEGRAM_CHAT_ID not set. Attempting to get from updates...")
            chat_id = await self.telegram.get_chat_id_from_updates()
            if chat_id:
                print(f"[INFO] Add this to your .env: TELEGRAM_CHAT_ID={chat_id}")
                self.telegram.chat_id = chat_id
            else:
                print("[ERROR] Could not get chat ID. Send a message to your bot first.")
                return
        
        success = await self.telegram.send_test_message()
        if success:
            print("[OK] Telegram bot is working!")
        else:
            print("[ERROR] Failed to send test message")
    
    async def cleanup_database(self, days: int = DATA_RETENTION_DAYS) -> None:
        """
        Clean up old trades to manage database size.
        Preserves high-score trades (insider_score >= 70) forever.
        """
        print(f"[CLEANUP] Running database cleanup (retaining {days} days)...")
        
        await init_database(self.db_path)
        self.repository = TradeRepository(self.db_path)
        
        # Get stats before
        stats_before = await self.repository.get_database_stats()
        print(f"[STATS] Before: {stats_before['total_trades']} trades")
        
        # Run cleanup
        deleted = await self.repository.cleanup_old_trades(days)
        print(f"[DELETE] Removed {deleted} old trades")
        
        # Vacuum to reclaim space
        print(f"[VACUUM] Reclaiming disk space...")
        await self.repository.vacuum_database()
        
        # Get stats after
        stats_after = await self.repository.get_database_stats()
        print(f"[STATS] After: {stats_after['total_trades']} trades")
        print(f"[STATS] High-score trades preserved: {stats_after['high_score_trades']}")
        print(f"[OK] Cleanup complete")


async def main() -> None:
    """Main entry point."""
    sentinel = InsiderSentinel()
    
    # Handle graceful shutdown
    def signal_handler(sig, frame):
        print("\n[WARN] Shutdown signal received...")
        asyncio.create_task(sentinel.stop())
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Parse command line arguments
    if len(sys.argv) > 1:
        if sys.argv[1] == "--backfill":
            # Backfill mode: python main.py --backfill <from_block> [to_block]
            from_block = int(sys.argv[2]) if len(sys.argv) > 2 else 50000000
            to_block = sys.argv[3] if len(sys.argv) > 3 else "latest"
            await sentinel.backfill(from_block, to_block)
            return
        elif sys.argv[1] == "--dry-run":
            print("[TEST] Dry run mode - testing connection...")
            async with AsyncWeb3(create_web3_provider(POLYGON_WSS_URL)) as w3:
                chain_id = await w3.eth.chain_id
                block = await w3.eth.block_number
                print(f"[OK] Connected to chain {chain_id} at block {block}")
            return
        elif sys.argv[1] == "--test-telegram":
            await sentinel.test_telegram()
            return
        elif sys.argv[1] == "--cleanup":
            # Cleanup mode: python main.py --cleanup [days]
            days = int(sys.argv[2]) if len(sys.argv) > 2 else DATA_RETENTION_DAYS
            await sentinel.cleanup_database(days)
            return
    
    # Normal streaming mode
    await sentinel.start()


if __name__ == "__main__":
    asyncio.run(main())
