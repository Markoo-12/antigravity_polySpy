"""
Trade repository for async database operations.
"""
import aiosqlite
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List


@dataclass
class Trade:
    """Represents a trade record."""
    tx_hash: str
    block_number: int
    timestamp: datetime
    order_hash: str
    proxy_address: str
    owner_address: Optional[str]
    proxy_type: Optional[str]
    asset_id: str
    side: str  # 'buy' or 'sell'
    amount_usdc: float
    price: Optional[float] = None
    market_id: Optional[str] = None
    id: Optional[int] = None


class TradeRepository:
    """Async repository for trade operations."""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
    
    async def insert_trade(self, trade: Trade) -> int:
        """
        Insert a new trade into the database.
        Returns the inserted row ID, or -1 if skipped (duplicate).
        """
        async with aiosqlite.connect(self.db_path) as db:
            try:
                cursor = await db.execute(
                    """
                    INSERT INTO trades (
                        tx_hash, block_number, timestamp, order_hash, 
                        proxy_address, owner_address, proxy_type, 
                        asset_id, side, amount_usdc, price, market_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        trade.tx_hash,
                        trade.block_number,
                        trade.timestamp.isoformat(),
                        trade.order_hash,
                        trade.proxy_address,
                        trade.owner_address,
                        trade.proxy_type,
                        trade.proxy_type,
                        trade.asset_id,
                        trade.side,
                        trade.amount_usdc,
                        trade.price,
                        trade.market_id,
                    ),
                )
                await db.commit()
                return cursor.lastrowid or -1
            except aiosqlite.IntegrityError:
                # Duplicate trade, skip
                return -1
    
    async def update_owner(self, trade_id: int, owner_address: str, proxy_type: str) -> None:
        """Update the resolved owner address for a trade."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE trades SET owner_address = ?, proxy_type = ? WHERE id = ?",
                (owner_address, proxy_type, trade_id),
            )
            await db.commit()
    
    async def get_trades_without_owner(self, limit: int = 100) -> List[Trade]:
        """Get trades where owner has not been resolved yet."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT * FROM trades 
                WHERE owner_address IS NULL 
                ORDER BY timestamp DESC 
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
            return [self._row_to_trade(row) for row in rows]
    
    async def get_recent_trades(self, limit: int = 50) -> List[Trade]:
        """Get most recent trades."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
            return [self._row_to_trade(row) for row in rows]
    
    async def get_trade_count(self) -> int:
        """Get total number of trades in database."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM trades")
            row = await cursor.fetchone()
            return row[0] if row else 0
    
    async def update_insider_score(
        self,
        trade_id: int,
        insider_score: int,
        score_reasons: str,
        bridge_funded: bool = False,
        bridge_name: Optional[str] = None,
        win_rate: Optional[float] = None,
    ) -> None:
        """Update the insider score and forensic analysis for a trade."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE trades SET 
                    insider_score = ?,
                    score_reasons = ?,
                    bridge_funded = ?,
                    bridge_name = ?,
                    win_rate = ?,
                    analyzed_at = ?
                WHERE id = ?
                """,
                (
                    insider_score,
                    score_reasons,
                    bridge_funded,
                    bridge_name,
                    win_rate,
                    datetime.utcnow().isoformat(),
                    trade_id,
                ),
            )
            await db.commit()
    
    async def get_high_score_trades(self, min_score: int = 70, limit: int = 50) -> List[Trade]:
        """Get trades with insider score at or above threshold."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT * FROM trades 
                WHERE insider_score >= ? 
                ORDER BY insider_score DESC, timestamp DESC 
                LIMIT ?
                """,
                (min_score, limit),
            )
            rows = await cursor.fetchall()
            return [self._row_to_trade(row) for row in rows]
    
    def _row_to_trade(self, row: aiosqlite.Row) -> Trade:
        """Convert a database row to a Trade object."""
        return Trade(
            id=row["id"],
            tx_hash=row["tx_hash"],
            block_number=row["block_number"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            order_hash=row["order_hash"],
            proxy_address=row["proxy_address"],
            owner_address=row["owner_address"],
            proxy_type=row["proxy_type"],
            asset_id=row["asset_id"],
            side=row["side"],
            amount_usdc=row["amount_usdc"],
            price=row["price"] if "price" in row.keys() else None,
            market_id=row["market_id"],
        )
    
    async def cleanup_old_trades(self, days: int = 30) -> int:
        """
        Delete trades older than specified days to manage database size.
        
        Args:
            days: Number of days to retain (default 30)
            
        Returns:
            Number of deleted rows
        """
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                DELETE FROM trades 
                WHERE timestamp < datetime('now', ?)
                AND insider_score < 70
                """,
                (f'-{days} days',)
            )
            deleted = cursor.rowcount
            await db.commit()
            return deleted
    
    async def get_database_stats(self) -> dict:
        """Get database statistics for monitoring."""
        async with aiosqlite.connect(self.db_path) as db:
            # Total trades
            cursor = await db.execute("SELECT COUNT(*) FROM trades")
            total = (await cursor.fetchone())[0]
            
            # High-score trades (these are never deleted)
            cursor = await db.execute(
                "SELECT COUNT(*) FROM trades WHERE insider_score >= 70"
            )
            high_score = (await cursor.fetchone())[0]
            
            # Oldest trade
            cursor = await db.execute(
                "SELECT MIN(timestamp) FROM trades"
            )
            oldest = (await cursor.fetchone())[0]
            
            return {
                "total_trades": total,
                "high_score_trades": high_score,
                "oldest_trade": oldest,
            }
    
    async def vacuum_database(self) -> None:
        """Reclaim disk space after deletions."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("VACUUM")

    async def check_flipping_activity(self, proxy_address: str, asset_id: str, minutes: int = 30) -> bool:
        """
        Check if wallet has bought and sold the same asset within the time window.
        Returns True if flipping detected (buy -> sell or sell -> buy < minutes).
        """
        async with aiosqlite.connect(self.db_path) as db:
            # Check for ANY trade of opposite side for same asset in time window
            # We look for trades in the last X minutes
            cursor = await db.execute(
                """
                SELECT COUNT(*) FROM trades 
                WHERE proxy_address = ? 
                AND asset_id = ? 
                AND timestamp >= datetime('now', ?)
                """,
                (proxy_address, asset_id, f'-{minutes} minutes')
            )
            count = (await cursor.fetchone())[0]
            # If count > 1, it means we have multiple trades (flipping or accumulation)
            # To be precise, we should check if they are opposite sides.
            # But checking > 1 trade in 30 mins for same asset is already suspicious "Active Trading"
            # The prompt says "buying and selling".
            
            if count > 1:
                # Check effectively for distinct sides
                cursor = await db.execute(
                    """
                    SELECT COUNT(DISTINCT side) FROM trades 
                    WHERE proxy_address = ? 
                    AND asset_id = ? 
                    AND timestamp >= datetime('now', ?)
                    """,
                    (proxy_address, asset_id, f'-{minutes} minutes')
                )
                sides = (await cursor.fetchone())[0]
                return sides > 1  # True if both BUY and SELL found
            
            return False

    async def get_position_hold_time(self, proxy_address: str, asset_id: str) -> float:
        """
        Get the duration (in minutes) the wallet has held the position.
        Returns minutes since first BUY. Returns 0 if no Buys found.
        """
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT MIN(timestamp) FROM trades 
                WHERE proxy_address = ? 
                AND asset_id = ? 
                AND side = 'buy'
                """,
                (proxy_address, asset_id)
            )
            row = await cursor.fetchone()
            if not row or not row[0]:
                return 0.0
                
            first_buy = datetime.fromisoformat(row[0])
            now = datetime.utcnow() # Assuming DB timestamps are UTC
            
            delta = now - first_buy
            return delta.total_seconds() / 60.0

    async def get_lifetime_volume(self, wallet_address: str) -> float:
        """Get total volume traded by a wallet."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT SUM(amount_usdc) FROM trades 
                WHERE proxy_address = ? OR owner_address = ?
                """,
                (wallet_address, wallet_address)
            )
            row = await cursor.fetchone()
            return row[0] if row and row[0] else 0.0

    async def get_max_position_value(self, wallet_address: str) -> float:
        """
        Get the maximum single position value held by a wallet.
        Approximated by the largest single BUY order for now, 
        or we could sum up buys per asset. 
        For 'Max Position Held', largest BUY is a safe lower bound proxy.
        """
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT MAX(amount_usdc) FROM trades 
                WHERE (proxy_address = ? OR owner_address = ?)
                AND side = 'buy'
                """,
                (wallet_address, wallet_address)
            )
            row = await cursor.fetchone()
            return row[0] if row and row[0] else 0.0

    async def get_recent_wallet_trades(self, wallet_address: str, minutes: int = 10) -> List[Trade]:
        """Get recent trades for a wallet to check for symmetric moves."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT * FROM trades 
                WHERE (proxy_address = ? OR owner_address = ?)
                AND timestamp >= datetime('now', ?)
                ORDER BY timestamp DESC
                """,
                (wallet_address, wallet_address, f'-{minutes} minutes')
            )
            rows = await cursor.fetchall()
            return [self._row_to_trade(row) for row in rows]

