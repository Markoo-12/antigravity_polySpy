"""Quick script to investigate suspicious patterns in trades.db"""
import sqlite3
from datetime import datetime

conn = sqlite3.connect(r'data/trades.db')
c = conn.cursor()

print("=" * 70)
print("INVESTIGATION: Suspicious Patterns Analysis")
print("=" * 70)

# 1. Large trades (>$100k)
print("\n1. WHALE TRADES (>$100k)")
print("-" * 70)
c.execute('''
    SELECT insider_score, CAST(amount_usdc as INTEGER), side, 
           datetime(timestamp), owner_address, score_reasons
    FROM trades 
    WHERE amount_usdc > 100000 
    ORDER BY amount_usdc DESC 
    LIMIT 10
''')
for r in c.fetchall():
    print(f"Score: {r[0]:3} | ${r[1]:>10,} | {r[2]} | {r[3]}")
    print(f"         Owner: {(r[4] or 'unknown')[:42]}")
    print(f"         Reasons: {r[5]}")
    print()

# 2. Coordinated trades (same timestamp)
print("\n2. COORDINATED TRADES (same timestamp, multiple wallets)")
print("-" * 70)
c.execute('''
    SELECT timestamp, COUNT(*) as cnt, 
           GROUP_CONCAT(DISTINCT owner_address) as wallets,
           CAST(SUM(amount_usdc) as INTEGER) as total_vol
    FROM trades 
    GROUP BY timestamp 
    HAVING COUNT(*) >= 3 
    ORDER BY cnt DESC, timestamp DESC 
    LIMIT 10
''')
for r in c.fetchall():
    print(f"Time: {r[0]} | {r[1]} trades | Total: ${r[3]:,}")
    wallets = (r[2] or "").split(",")
    unique_wallets = len(set(wallets))
    print(f"         Unique wallets: {unique_wallets}")
    print()

# 3. Same amount trades (potential coordination)
print("\n3. SAME AMOUNT TRADES (potential coordination)")
print("-" * 70)
c.execute('''
    SELECT CAST(amount_usdc as INTEGER) as amt, COUNT(*) as cnt,
           GROUP_CONCAT(DISTINCT owner_address) as wallets
    FROM trades 
    WHERE amount_usdc > 10000
    GROUP BY CAST(amount_usdc as INTEGER)
    HAVING COUNT(*) >= 3 
    ORDER BY cnt DESC 
    LIMIT 10
''')
for r in c.fetchall():
    wallets = (r[2] or "").split(",")
    unique_wallets = len(set(wallets))
    print(f"Amount: ${r[0]:>10,} | {r[1]} trades | {unique_wallets} unique wallets")

# 4. The $199,999 trade specifically
print("\n4. THE $199,999 TRADE")
print("-" * 70)
c.execute('''
    SELECT * FROM trades 
    WHERE amount_usdc BETWEEN 199000 AND 200000
''')
cols = [d[0] for d in c.description]
for row in c.fetchall():
    for col, val in zip(cols, row):
        print(f"  {col}: {val}")
    print()

conn.close()
print("=" * 70)
