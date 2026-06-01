import asyncio
import logging
import time
import os
import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BET_AMOUNT = 0.001

bot_sessions = {}
user_wallets = {}

def get_session(user_id):
    if user_id not in bot_sessions:
        bot_sessions[user_id] = {
            "running": False,
            "test_mode": False,
            "balance": 0.0,
            "initial_balance": 0.0,
            "wins": 0,
            "losses": 0,
            "total_rounds": 0,
            "profit": 0.0,
            "consecutive_losses": 0,
            "current_streak": 0,
            "max_consecutive_wins": 0,
            "max_consecutive_losses": 0,
            "stop_profit_pct": 100,
            "stop_loss_pct": 50,
            "max_rounds": 0,
            "waiting_for": None,
        }
    return bot_sessions[user_id]

# ═══════════════════════════════════════
# WALLET
# ═══════════════════════════════════════
def init_wallet(user_id, address, private_key):
    from cryptography.fernet import Fernet
    import hashlib, base64
    raw = hashlib.sha256((str(user_id) + "bnbsalt2024").encode()).digest()
    key = base64.urlsafe_b64encode(raw)
    user_wallets[user_id] = {
        "address": address,
        "encrypted_key": Fernet(key).encrypt(private_key.encode()).decode()
    }

def get_wallet(user_id):
    return user_wallets.get(user_id)

def get_private_key(user_id):
    from cryptography.fernet import Fernet
    import hashlib, base64
    w = user_wallets.get(user_id)
    if not w: return None
    raw = hashlib.sha256((str(user_id) + "bnbsalt2024").encode()).digest()
    key = base64.urlsafe_b64encode(raw)
    return Fernet(key).decrypt(w["encrypted_key"].encode()).decode()

def wallet_from_seed(phrase):
    try:
        from eth_account import Account
        Account.enable_unaudited_hdwallet_features()
        acc = Account.from_mnemonic(phrase.strip())
        return {"address": acc.address, "private_key": acc.key.hex()}
    except Exception as e:
        logger.error(f"Seed error: {e}")
        return None

def wallet_from_privkey(pk):
    try:
        from eth_account import Account
        if not pk.startswith("0x"): pk = "0x" + pk
        acc = Account.from_key(pk)
        return {"address": acc.address, "private_key": pk}
    except Exception as e:
        logger.error(f"Privkey error: {e}")
        return None

# ═══════════════════════════════════════
# WEB3
# ═══════════════════════════════════════
PREDICTION_ADDRESS = "0x18B2A687610328590Bc8F2e5fEdDe3b582A49cdA"

PREDICTION_ABI = [
    {"inputs":[{"internalType":"uint256","name":"epoch","type":"uint256"}],
     "name":"betBull","outputs":[],"stateMutability":"payable","type":"function"},
    {"inputs":[{"internalType":"uint256","name":"epoch","type":"uint256"}],
     "name":"betBear","outputs":[],"stateMutability":"payable","type":"function"},
    {"inputs":[{"internalType":"uint256[]
