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
                        asset_id, side, amount_usdc, market_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        trade.tx_hash,
                        trade.block_number,
                        trade.timestamp.isoformat(),
                        trade.order_hash,
                        trade.proxy_address,
                        trade.owner_address,
                        trade.proxy_type,
                        trade.asset_id,
                        trade.side,
                        trade.amount_usdc,
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
