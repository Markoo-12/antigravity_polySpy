"""
Configuration module for Polymarket Insider Sentinel.
Loads environment variables and defines contract constants.
"""
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# RPC Configuration
POLYGON_WSS_URL = os.getenv("POLYGON_WSS_URL", "")
POLYGON_HTTP_URL = os.getenv("POLYGON_HTTP_URL", "https://polygon-rpc.com")

# Trade filtering
USDC_THRESHOLD = float(os.getenv("USDC_THRESHOLD", "3000"))
USDC_DECIMALS = 6  # USDC has 6 decimal places

# CTF Exchange Contract
CTF_EXCHANGE_ADDRESS = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"

# OrderFilled Event Signature (VERIFIED from live blockchain data)
# Topic0 observed: 0xd0a08e8c493f9c94f29311604c9de1b4e8c8d4c06bd0c789af57f2d65bfec0f6
ORDER_FILLED_TOPIC = "0xd0a08e8c493f9c94f29311604c9de1b4e8c8d4c06bd0c789af57f2d65bfec0f6"

# ABI for OrderFilled event decoding
ORDER_FILLED_ABI = {
    "anonymous": False,
    "inputs": [
        {"indexed": True, "name": "orderHash", "type": "bytes32"},
        {"indexed": True, "name": "maker", "type": "address"},
        {"indexed": True, "name": "taker", "type": "address"},
        {"indexed": False, "name": "makerAssetId", "type": "uint256"},
        {"indexed": False, "name": "takerAssetId", "type": "uint256"},
        {"indexed": False, "name": "makerAmountFilled", "type": "uint256"},
        {"indexed": False, "name": "takerAmountFilled", "type": "uint256"},
        {"indexed": False, "name": "fee", "type": "uint256"},
    ],
    "name": "OrderFilled",
    "type": "event",
}

# Gnosis Safe ABI (minimal for getOwners)
GNOSIS_SAFE_ABI = [
    {
        "constant": True,
        "inputs": [],
        "name": "getOwners",
        "outputs": [{"name": "", "type": "address[]"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function",
    }
]

# Polymarket Proxy ABI (for owner lookup)
POLYMARKET_PROXY_ABI = [
    {
        "constant": True,
        "inputs": [],
        "name": "owner",
        "outputs": [{"name": "", "type": "address"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function",
    }
]

# Database path
DATABASE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "trades.db")

# =============================================================================
# PHASE 3 & 4 CONFIGURATION
# =============================================================================

# Moralis API
MORALIS_API_KEY = os.getenv("MORALIS_API_KEY", "")
MORALIS_BASE_URL = "https://deep-index.moralis.io/api/v2.2"

# Telegram Bot
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Bridge Contract Addresses (Polygon)
BRIDGE_CONTRACTS = {
    "Across": "0x9295ee87aA57Fe2D0BA461B29758E470fE805555",
    "Stargate V2": "0x1205f31718499dBf1fCb446663B520132b67d020",
    "Synapse": "0x7E7A0e201FD38d3ADAA9523daf6C10B33C249f67",
}

# Insider Score Thresholds
INSIDER_ALERT_THRESHOLD = 70  # Send alert if score >= this
FORENSIC_USDC_THRESHOLD = float(os.getenv("FORENSIC_USDC_THRESHOLD", "10000"))  # Only analyze trades > this for insider
BRIDGE_TIME_WINDOW_HOURS = 4  # Check for bridge txns within this window
WIN_RATE_THRESHOLD = 0.75  # 75% win rate threshold
WIN_RATE_POSITION_MIN = 5000  # Minimum position size for win rate calc

# Scoring Points
SCORE_BRIDGE_FUNDED = 40  # Points for being funded via bridge
SCORE_HIGH_WIN_RATE = 30  # Points for >75% win rate
SCORE_QUIET_ACCUMULATION = 30  # Points for large trade in quiet market

# Market Velocity Thresholds
VOLUME_SHARE_THRESHOLD = 0.10  # Trade must be >10% of market volume
PRICE_CHANGE_THRESHOLD = 0.02  # Price must have moved <2% in last hour

# =============================================================================
# PHASE 5: PROFIT-LOGIC LAYER CONFIGURATION
# =============================================================================

# Upside Validator
PRICE_CEILING = 0.70  # Max price to consider (70 cents)
FOLLOWER_TRADE_SIZE = 2000  # $2,000 test trade for slippage calc
MAX_SLIPPAGE_PERCENT = 0.03  # 3% max acceptable slippage
SLIPPAGE_SCORE_PENALTY = 40  # Score penalty for high slippage
ALPHA_GAP_MIN = 0.08  # Minimum gap between entry and current (8 cents)

# Late-Stage Sentinel
MATURE_MARKET_DAYS = 21  # Market must be older than this
STAGNANT_HOURS = 48  # Price must be stable for this long
LATE_STAGE_TRADE_MIN = 20000  # Minimum trade size for late-stage bonus
LATE_STAGE_SCORE_BONUS = 60  # Points added for late-stage pattern

# Execution Guard
MONITOR_DURATION_MINUTES = 60  # Monitor whale for 60 minutes
DUMP_THRESHOLD_PERCENT = 0.20  # Alert if 20%+ sold within monitoring period
MONITOR_CHECK_INTERVAL = 300  # Check every 5 minutes (in seconds)

# Cluster Detection
CLUSTER_WINDOW_MINUTES = 10  # Rolling window size
CLUSTER_THRESHOLD_WALLETS = 3  # Minimum wallets to trigger cluster
CLUSTER_TIME_WINDOW_MINUTES = 5  # Time window for cluster detection
CLUSTER_MIN_SCORE = 70  # Minimum insider score for cluster participants

# =============================================================================
# DATABASE MAINTENANCE
# =============================================================================

# Data Retention (automatic cleanup)
DATA_RETENTION_DAYS = int(os.getenv("DATA_RETENTION_DAYS", "30"))  # Keep trades for 30 days
