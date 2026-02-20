"""Quick test to fire a Telegram notification."""
import asyncio
import sys
sys.path.insert(0, ".")

from src.alerts.telegram_bot import TelegramAlertBot

async def main():
    bot = TelegramAlertBot()
    print(f"Token set: {bool(bot.bot_token)}")
    print(f"Chat ID set: {bool(bot.chat_id)}")
    if bot.bot_token:
        print(f"Token (last 5): ...{bot.bot_token[-5:]}")
    else:
        print("ERROR: TELEGRAM_BOT_TOKEN is empty!")
    if bot.chat_id:
        print(f"Chat ID: {bot.chat_id}")
    else:
        print("ERROR: TELEGRAM_CHAT_ID is empty!")
    
    if not bot.bot_token or not bot.chat_id:
        print("\nFix your .env file and try again.")
        return
    
    print("\nSending test message...")
    result = await bot.send_test_message()
    print(f"Result: {'SUCCESS' if result else 'FAILED'}")

if __name__ == "__main__":
    asyncio.run(main())
