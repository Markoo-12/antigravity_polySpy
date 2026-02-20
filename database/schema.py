"""
SQLite database schema for trades.
"""
import aiosqlite
import os
from pathlib import Path

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tx_hash TEXT NOT NULL,
    block_number INTEGER NOT NULL,
    timestamp DATETIME NOT NULL,
    order_hash TEXT NOT NULL,
    proxy_address TEXT NOT NULL,
    owner_address TEXT,
    proxy_type TEXT,
    asset_id TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('buy', 'sell')),
    amount_usdc REAL NOT NULL,
    market_id TEXT,
    -- Phase 3: Forensic Auditor fields
    insider_score INTEGER DEFAULT 0,
    score_reasons TEXT,  -- JSON array of reasons
    bridge_funded BOOLEAN DEFAULT 0,
    bridge_name TEXT,
    win_rate REAL,
    analyzed_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(tx_hash, order_hash, proxy_address)
);

CREATE INDEX IF NOT EXISTS idx_trades_owner_address ON trades(owner_address);
CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp);
CREATE INDEX IF NOT EXISTS idx_trades_amount ON trades(amount_usdc);
CREATE INDEX IF NOT EXISTS idx_trades_block ON trades(block_number);
CREATE INDEX IF NOT EXISTS idx_trades_insider_score ON trades(insider_score);
"""

# Migration to add new columns to existing tables
MIGRATION_SQL = """
-- Add Phase 3 columns if they don't exist
ALTER TABLE trades ADD COLUMN insider_score INTEGER DEFAULT 0;
ALTER TABLE trades ADD COLUMN score_reasons TEXT;
ALTER TABLE trades ADD COLUMN bridge_funded BOOLEAN DEFAULT 0;
ALTER TABLE trades ADD COLUMN bridge_name TEXT;
ALTER TABLE trades ADD COLUMN win_rate REAL;
ALTER TABLE trades ADD COLUMN analyzed_at DATETIME;
"""


async def init_database(db_path: str) -> None:
    """
    Initialize the SQLite database with the trades schema.
    Creates parent directories if needed.
    
    Args:
        db_path: Path to the SQLite database file.
    """
    # Ensure parent directory exists
    db_dir = Path(db_path).parent
    db_dir.mkdir(parents=True, exist_ok=True)
    
    async with aiosqlite.connect(db_path) as db:
        # Enable WAL mode for better concurrency and performance
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.execute("PRAGMA cache_size=-64000")  # 64MB cache
        
        await db.executescript(SCHEMA_SQL)
        await db.commit()
        print(f"[OK] Database initialized at {db_path} (WAL mode enabled)")
