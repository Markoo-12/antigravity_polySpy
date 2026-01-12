"""
Telegram bot for sending insider trading alerts.

Enhanced with:
- Direct execution links with market slug and outcome
- Inline keyboard buttons for quick trade execution
- Special alert types: CONVICTION CLUSTER, MANIPULATION WARNING
"""
import aiohttp
import json
from typing import Optional, List
from dataclasses import dataclass, field

from ..config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID


@dataclass
class AlertData:
    """Data for a Telegram alert."""
    insider_score: int
    trade_amount_usdc: float
    side: str  # 'buy' or 'sell'
    asset_id: str
    owner_address: str
    proxy_address: str
    tx_hash: str
    reasons: List[str]
    market_id: Optional[str] = None
    market_slug: Optional[str] = None  # NEW: For execution link
    outcome: Optional[str] = None  # NEW: 'Yes' or 'No'
    current_price: Optional[float] = None  # NEW: Current market price


@dataclass
class ClusterAlertData:
    """Data for a conviction cluster alert."""
    asset_id: str
    wallets: List[str]
    total_amount_usdc: float
    avg_score: float
    time_span_seconds: int
    market_slug: Optional[str] = None
    outcome: Optional[str] = None


@dataclass 
class DumpAlertData:
    """Data for a manipulation warning alert."""
    wallet_address: str
    asset_id: str
    initial_shares: float
    sold_shares: float
    dump_percent: float
    minutes_after_buy: int
    tx_hash: str


class TelegramAlertBot:
    """
    Sends formatted alerts to Telegram when insider activity is detected.
    
    Enhanced with inline buttons for trade execution.
    """
    
    def __init__(
        self,
        bot_token: str = TELEGRAM_BOT_TOKEN,
        chat_id: str = TELEGRAM_CHAT_ID,
    ):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_base = f"https://api.telegram.org/bot{bot_token}"
    
    def is_configured(self) -> bool:
        """Check if the bot is properly configured."""
        return bool(self.bot_token and self.chat_id)
    
    async def send_alert(self, alert: AlertData) -> bool:
        """
        Send an insider alert to Telegram with execution button.
        
        Args:
            alert: AlertData with trade and score info
            
        Returns:
            True if sent successfully
        """
        if not self.is_configured():
            print("[WARN] Telegram bot not configured (missing token or chat_id)")
            return False
        
        message = self._format_alert(alert)
        keyboard = self._build_execution_keyboard(alert)
        
        return await self._send_message(message, keyboard)
    
    async def send_cluster_alert(self, cluster: ClusterAlertData) -> bool:
        """
        Send a CONVICTION CLUSTER alert.
        
        Args:
            cluster: ClusterAlertData with cluster info
            
        Returns:
            True if sent successfully
        """
        if not self.is_configured():
            return False
        
        message = self._format_cluster_alert(cluster)
        keyboard = self._build_cluster_keyboard(cluster)
        
        return await self._send_message(message, keyboard)
    
    async def send_dump_warning(self, dump: DumpAlertData) -> bool:
        """
        Send a MANIPULATION WARNING alert.
        
        Args:
            dump: DumpAlertData with dump detection info
            
        Returns:
            True if sent successfully
        """
        if not self.is_configured():
            return False
        
        message = self._format_dump_warning(dump)
        return await self._send_message(message)
    
    def _format_alert(self, alert: AlertData) -> str:
        """Format the standard insider alert message."""
        # Emoji based on score severity
        if alert.insider_score >= 90:
            emoji = "🚨🚨🚨"
        elif alert.insider_score >= 80:
            emoji = "🚨🚨"
        else:
            emoji = "🚨"
        
        # Side emoji
        side_emoji = "🟢" if alert.side == "buy" else "🔴"
        
        # Build market link
        if alert.market_slug:
            market_link = f"https://polymarket.com/event/{alert.market_slug}"
        elif alert.market_id:
            market_link = f"https://polymarket.com/event/{alert.market_id}"
        else:
            market_link = "https://polymarket.com"
        
        # Build Arkham Intelligence link
        arkham_link = f"https://platform.arkhamintelligence.com/explorer/address/{alert.owner_address}"
        
        # Polygonscan link
        polygonscan_link = f"https://polygonscan.com/tx/{alert.tx_hash}"
        
        # Format reasons
        reasons_text = "\n".join([f"  • {r}" for r in alert.reasons]) if alert.reasons else "  • No specific flags"
        
        # Price info
        price_info = ""
        if alert.current_price is not None:
            price_info = f"\n💰 *Current Price:* {alert.current_price:.0%}"
        
        message = f"""
{emoji} *INSIDER ALERT* (Score: {alert.insider_score}/100)

{side_emoji} *Trade:* ${alert.trade_amount_usdc:,.2f} USDC ({alert.side.upper()}){price_info}

👛 *Wallet:* `{alert.owner_address[:8]}...{alert.owner_address[-6:]}`

📊 *Reasons:*
{reasons_text}

🔗 *Links:*
  • [Polymarket]({market_link})
  • [Arkham Intelligence]({arkham_link})
  • [Transaction]({polygonscan_link})
"""
        return message.strip()
    
    def _format_cluster_alert(self, cluster: ClusterAlertData) -> str:
        """Format a conviction cluster alert."""
        wallet_list = "\n".join([f"  • `{w[:8]}...{w[-6:]}`" for w in cluster.wallets[:5]])
        if len(cluster.wallets) > 5:
            wallet_list += f"\n  • ...and {len(cluster.wallets) - 5} more"
        
        message = f"""
🚨🚨🚨 *CONVICTION CLUSTER DETECTED*

⚡ *{len(cluster.wallets)} wallets* entered the same position in {cluster.time_span_seconds}s!

💰 *Combined Volume:* ${cluster.total_amount_usdc:,.2f} USDC
📊 *Average Score:* {cluster.avg_score:.0f}/100

👛 *Wallets:*
{wallet_list}

⚠️ This is the *highest quality* insider signal!
"""
        return message.strip()
    
    def _format_dump_warning(self, dump: DumpAlertData) -> str:
        """Format a manipulation warning alert."""
        message = f"""
⚠️⚠️⚠️ *MANIPULATION WARNING*

🔴 Whale sold *{dump.dump_percent:.0%}* of position within {dump.minutes_after_buy} minutes!

👛 *Wallet:* `{dump.wallet_address[:8]}...{dump.wallet_address[-6:]}`
📉 *Sold:* {dump.sold_shares:,.0f} shares of {dump.initial_shares:,.0f}

🚫 *DO NOT FOLLOW* - Possible pump-and-dump detected!

🔗 [View Transaction](https://polygonscan.com/tx/{dump.tx_hash})
"""
        return message.strip()
    
    def _build_execution_keyboard(self, alert: AlertData) -> Optional[dict]:
        """Build inline keyboard with execution button."""
        if not alert.market_slug:
            return None
        
        # Build execution URL
        outcome_param = ""
        if alert.outcome:
            outcome_param = f"?outcome={alert.outcome}"
        
        execution_url = f"https://polymarket.com/event/{alert.market_slug}{outcome_param}"
        
        keyboard = {
            "inline_keyboard": [
                [
                    {
                        "text": "🚀 EXECUTE TRADE",
                        "url": execution_url,
                    }
                ],
                [
                    {
                        "text": "🔍 View Wallet",
                        "url": f"https://platform.arkhamintelligence.com/explorer/address/{alert.owner_address}",
                    },
                    {
                        "text": "📜 View TX",
                        "url": f"https://polygonscan.com/tx/{alert.tx_hash}",
                    }
                ]
            ]
        }
        
        return keyboard
    
    def _build_cluster_keyboard(self, cluster: ClusterAlertData) -> Optional[dict]:
        """Build inline keyboard for cluster alert."""
        if not cluster.market_slug:
            return None
        
        outcome_param = ""
        if cluster.outcome:
            outcome_param = f"?outcome={cluster.outcome}"
        
        execution_url = f"https://polymarket.com/event/{cluster.market_slug}{outcome_param}"
        
        keyboard = {
            "inline_keyboard": [
                [
                    {
                        "text": "🚀🚀 HIGH-CONVICTION TRADE",
                        "url": execution_url,
                    }
                ]
            ]
        }
        
        return keyboard
    
    async def _send_message(
        self, 
        text: str, 
        reply_markup: Optional[dict] = None,
    ) -> bool:
        """Send a message via Telegram Bot API."""
        url = f"{self.api_base}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status == 200:
                        print(f"[SENT] Alert sent to Telegram")
                        return True
                    else:
                        error = await resp.text()
                        print(f"[ERROR] Telegram API error: {resp.status} - {error}")
                        return False
        except Exception as e:
            print(f"[ERROR] Error sending Telegram message: {e}")
            return False
    
    async def send_test_message(self) -> bool:
        """Send a test message to verify bot configuration."""
        test_msg = """
🧪 *Polymarket Insider Sentinel - Test Alert*

✅ Bot is configured correctly!

This is a test message to verify your Telegram integration.
"""
        return await self._send_message(test_msg.strip())
    
    async def get_chat_id_from_updates(self) -> Optional[str]:
        """
        Helper to get chat ID from recent messages.
        Message the bot first, then call this.
        """
        if not self.bot_token:
            print("[WARN] Bot token not configured")
            return None
        
        url = f"{self.api_base}/getUpdates"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        updates = data.get("result", [])
                        if updates:
                            # Get most recent message
                            latest = updates[-1]
                            chat_id = latest.get("message", {}).get("chat", {}).get("id")
                            if chat_id:
                                print(f"[INFO] Found chat ID: {chat_id}")
                                return str(chat_id)
                        print("[ERROR] No messages found. Send a message to the bot first.")
                    else:
                        error = await resp.text()
                        print(f"[ERROR] Telegram API error: {resp.status} - {error}")
        except Exception as e:
            print(f"[ERROR] Error getting updates: {e}")
        
        return None
