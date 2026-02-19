"""
Blacklist Repository - Manages blocked wallets and bots.
"""
import aiosqlite
import logging
from dataclasses import dataclass
from typing import Optional

@dataclass
class BlacklistEntry:
    """Entry in the blacklist."""
    address: str
    type: str  # 'bot', 'wash', 'flipper'
    count: int
    is_blocked: bool
    last_updated: Optional[str] = None


class BlacklistRepository:
    """Async repository for blacklist operations."""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        
    async def init_table(self):
        """Initialize the blacklist table."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS blacklist (
                    address TEXT PRIMARY KEY,
                    type TEXT,
                    count INTEGER DEFAULT 1,
                    is_blocked BOOLEAN DEFAULT 0,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await db.commit()
            
    async def is_blocked(self, address: str) -> bool:
        """Check if an address is permanently blocked."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT is_blocked FROM blacklist WHERE address = ?",
                (address.lower(),)
            )
            row = await cursor.fetchone()
            return bool(row[0]) if row else False
            
    async def flag_address(self, address: str, reason_type: str) -> bool:
        """
        Flag an address for suspicious activity.
        Returns True if the address is now BLOCKED (count > 3).
        """
        address = address.lower()
        async with aiosqlite.connect(self.db_path) as db:
            # Check existing
            cursor = await db.execute(
                "SELECT count, is_blocked FROM blacklist WHERE address = ?",
                (address,)
            )
            row = await cursor.fetchone()
            
            if row:
                count, is_blocked = row
                if is_blocked:
                    return True
                
                new_count = count + 1
                should_block = new_count > 3
                
                await db.execute(
                    """
                    UPDATE blacklist 
                    SET count = ?, is_blocked = ?, last_updated = CURRENT_TIMESTAMP, type = ?
                    WHERE address = ?
                    """,
                    (new_count, should_block, reason_type, address)
                )
                await db.commit()
                
                if should_block:
                    print(f"[BLACKLIST] [BLOCKED] {address[:10]}... (Flags: {new_count})")
                
                return should_block
            else:
                # First offense
                await db.execute(
                    """
                    INSERT INTO blacklist (address, type, count, is_blocked)
                    VALUES (?, ?, 1, 0)
                    """,
                    (address, reason_type)
                )
                await db.commit()
                return False

    async def get_all_blocked(self) -> list[str]:
        """Get all blocked addresses."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT address FROM blacklist WHERE is_blocked = 1"
            )
            rows = await cursor.fetchall()
            return [row[0] for row in rows]
