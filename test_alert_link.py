"""
Script to test the new Telegram alert links with real market data.
"""
import asyncio
import aiohttp
from src.alerts import TelegramAlertBot
from src.alerts.telegram_bot import AlertData
from src.forensic.market_resolver import MarketResolver
from src.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

async def main():
    print("[TEST] Testing Telegram Alert Links...")
    
    # 1. Fetch a real market from Gamma API to get a valid asset ID
    print("   Fetching a live market from Gamma API...")
    clob_token_id = None
    market_title = "Unknown"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://gamma-api.polymarket.com/markets?limit=1&active=true&closed=false") as resp:
                if resp.status == 200:
                    markets = await resp.json()
                    if markets:
                        market = markets[0]
                        import json
                        market_title = market.get("question")
                        tokens = market.get("clobTokenIds", [])
                        
                        if isinstance(tokens, str):
                            try:
                                tokens = json.loads(tokens)
                            except:
                                pass
                        
                        if tokens and isinstance(tokens, list):
                            clob_token_id = tokens[0] # Use the "Yes" token typically
                            print(f"   [FOUND] Market: {market_title}")
                            print(f"   [FOUND] Asset ID: {clob_token_id}")
    except Exception as e:
        print(f"   [ERROR] Failed to fetch market: {e}")
        return

    if not clob_token_id:
        print("   [ERROR] No active market found to test with.")
        return

    # 2. Resolve it using our new MarketResolver (to test the integration)
    print("   Resolving market slug...")
    resolver = MarketResolver()
    market_info = await resolver.resolve(clob_token_id)
    
    print(f"   [RESOLVED] Slug: {market_info.slug}")
    print(f"   [RESOLVED] Outcome: {market_info.outcome}")
    
    if not market_info.slug:
        print("   [FAIL] Could not resolve slug.")
        return

    # 3. Send Test Alert
    print("   Sending Telegram alert...")
    bot = TelegramAlertBot(bot_token=TELEGRAM_BOT_TOKEN, chat_id=TELEGRAM_CHAT_ID)
    
    if not bot.is_configured():
        print("   [ERROR] Bot not configured in .env")
        return
        
    # Mock Alert Data using the real market info
    alert = AlertData(
        insider_score=88, # High score for impact
        trade_amount_usdc=150000.0,
        side="buy",
        asset_id=clob_token_id,
        owner_address="0x1234567890abcdef1234567890abcdef12345678", # Fake
        proxy_address="0xabcdef1234567890abcdef1234567890abcdef12", # Fake
        tx_hash="0x" + "1"*64, # Fake
        reasons=[
            "Heavy accumulation in short window",
            "History of 85% win rate", 
            f"Trading on: {market_title}"
        ],
        market_id=market_info.condition_id,
        market_slug=market_info.slug,
        outcome=market_info.outcome,
        current_price=0.65
    )
    
    success = await bot.send_alert(alert)
    
    if success:
        print("\n[OK] Test message sent!")
        print(f"[LINK] Check your Telegram for the link: https://polymarket.com/event/{market_info.slug}")
    else:
        print("\n[FAIL] Failed to send message.")

if __name__ == "__main__":
    asyncio.run(main())
