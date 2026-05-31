import asyncio
import logging
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)
import os
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")

import json

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════
# STATE MANAGEMENT
# ═══════════════════════════════════════
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
            "gas_used": 0.0,
            "waiting_for": None,
            "last_prediction": None,
            "last_bet_amount": 0.0,
        }
    return bot_sessions[user_id]

# ═══════════════════════════════════════
# WALLET FUNCTIONS
# ═══════════════════════════════════════
def init_wallet(user_id, address, private_key):
    from cryptography.fernet import Fernet
    import hashlib, base64
    raw = hashlib.sha256((str(user_id) + "bnbsalt2024").encode()).digest()
    key = base64.urlsafe_b64encode(raw)
    f = Fernet(key)
    encrypted = f.encrypt(private_key.encode()).decode()
    user_wallets[user_id] = {
        "address": address,
        "encrypted_key": encrypted
    }

def get_wallet(user_id):
    return user_wallets.get(user_id)

def get_private_key(user_id):
    from cryptography.fernet import Fernet
    import hashlib, base64
    wallet = user_wallets.get(user_id)
    if not wallet:
        return None
    raw = hashlib.sha256((str(user_id) + "bnbsalt2024").encode()).digest()
    key = base64.urlsafe_b64encode(raw)
    f = Fernet(key)
    return f.decrypt(wallet["encrypted_key"].encode()).decode()

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
        if not pk.startswith("0x"):
            pk = "0x" + pk
        acc = Account.from_key(pk)
        return {"address": acc.address, "private_key": pk}
    except Exception as e:
        logger.error(f"Privkey error: {e}")
        return None

# ═══════════════════════════════════════
# BSC / WEB3 FUNCTIONS
# ═══════════════════════════════════════
def get_w3():
    from web3 import Web3
    for rpc in [
        "https://bsc-dataseed1.binance.org/",
        "https://bsc-dataseed2.binance.org/",
        "https://bsc-dataseed3.binance.org/",
    ]:
        try:
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 10}))
            if w3.is_connected():
                return w3
        except:
            continue
    return None

def get_bnb_balance(address):
    try:
        from web3 import Web3
        w3 = get_w3()
        if not w3:
            return 0.0
        bal = w3.eth.get_balance(Web3.to_checksum_address(address))
        return round(float(w3.from_wei(bal, "ether")), 6)
    except Exception as e:
        logger.error(f"Balance error: {e}")
        return 0.0

PREDICTION_ADDRESS = "0x18B2A687610328590Bc8F2e5fEdDe3b582A49cdA"
CHAINLINK_ADDRESS = "0x0567F2323251f0Aab15c8dFb1967E4e8A7D42aeE"

PREDICTION_ABI = [
    {"inputs": [{"internalType": "uint256", "name": "epoch", "type": "uint256"}],
     "name": "betBull", "outputs": [], "stateMutability": "payable", "type": "function"},
    {"inputs": [{"internalType": "uint256", "name": "epoch", "type": "uint256"}],
     "name": "betBear", "outputs": [], "stateMutability": "payable", "type": "function"},
    {"inputs": [{"internalType": "uint256[]", "name": "epochs", "type": "uint256[]"}],
     "name": "claim", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [], "name": "currentEpoch",
     "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
     "name": "rounds",
     "outputs": [
         {"internalType": "uint256", "name": "epoch", "type": "uint256"},
         {"internalType": "uint256", "name": "startTimestamp", "type": "uint256"},
         {"internalType": "uint256", "name": "lockTimestamp", "type": "uint256"},
         {"internalType": "uint256", "name": "closeTimestamp", "type": "uint256"},
         {"internalType": "int256", "name": "lockPrice", "type": "int256"},
         {"internalType": "int256", "name": "closePrice", "type": "int256"},
         {"internalType": "uint256", "name": "lockOracleid", "type": "uint256"},
         {"internalType": "uint256", "name": "closeOracleid", "type": "uint256"},
         {"internalType": "uint256", "name": "totalAmount", "type": "uint256"},
         {"internalType": "uint256", "name": "bullAmount", "type": "uint256"},
         {"internalType": "uint256", "name": "bearAmount", "type": "uint256"},
         {"internalType": "address", "name": "rewardBaseCalAmount", "type": "address"},
         {"internalType": "uint256", "name": "rewardAmount", "type": "uint256"},
         {"internalType": "bool", "name": "oracleCalled", "type": "bool"}
     ],
     "stateMutability": "view", "type": "function"},
    {"inputs": [{"internalType": "uint256", "name": "epoch", "type": "uint256"},
                {"internalType": "address", "name": "user", "type": "address"}],
     "name": "ledger",
     "outputs": [
         {"internalType": "uint8", "name": "position", "type": "uint8"},
         {"internalType": "uint256", "name": "amount", "type": "uint256"},
         {"internalType": "bool", "name": "claimed", "type": "bool"}
     ],
     "stateMutability": "view", "type": "function"},
]

CHAINLINK_ABI = [
    {"inputs": [], "name": "latestRoundData",
     "outputs": [
         {"internalType": "uint80", "name": "roundId", "type": "uint80"},
         {"internalType": "int256", "name": "answer", "type": "int256"},
         {"internalType": "uint256", "name": "startedAt", "type": "uint256"},
         {"internalType": "uint256", "name": "updatedAt", "type": "uint256"},
         {"internalType": "uint80", "name": "answeredInRound", "type": "uint80"}
     ],
     "stateMutability": "view", "type": "function"}
]

def get_contracts(w3):
    from web3 import Web3
    pred = w3.eth.contract(
        address=Web3.to_checksum_address(PREDICTION_ADDRESS),
        abi=PREDICTION_ABI
    )
    cl = w3.eth.contract(
        address=Web3.to_checksum_address(CHAINLINK_ADDRESS),
        abi=CHAINLINK_ABI
    )
    return pred, cl

# ═══════════════════════════════════════
# ANALYSIS FUNCTIONS
# ═══════════════════════════════════════
def get_klines(symbol="BNBUSDT", interval="1m", limit=100):
    import requests
    try:
        r = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=10
        )
        data = r.json()
        import pandas as pd
        df = pd.DataFrame(data, columns=[
            "time","open","high","low","close","volume",
            "close_time","quote_vol","trades","taker_buy_base","taker_buy_quote","ignore"
        ])
        for col in ["open","high","low","close","volume"]:
            df[col] = pd.to_numeric(df[col])
        return df
    except Exception as e:
        logger.error(f"Klines error: {e}")
        return None

def analyze(df):
    try:
        import numpy as np
        from ta.momentum import RSIIndicator
        from ta.trend import MACD, EMAIndicator
        from ta.volatility import BollingerBands

        if df is None or len(df) < 30:
            return None

        close = df["close"]
        ema9 = EMAIndicator(close, window=9).ema_indicator()
        ema21 = EMAIndicator(close, window=21).ema_indicator()
        rsi = RSIIndicator(close, window=14).rsi()
        macd = MACD(close)
        bb = BollingerBands(close)

        rsi_val = round(float(rsi.iloc[-1]), 1)
        macd_diff = float(macd.macd_diff().iloc[-1])
        bb_mid = float(bb.bollinger_mavg().iloc[-1])
        current = float(close.iloc[-1])
        vol_avg = float(df["volume"].tail(10).mean())
        vol_cur = float(df["volume"].iloc[-1])
        price_chg = (current - float(close.iloc[-6])) / float(close.iloc[-6]) * 100

        signals = {
            "ema": "UP" if float(ema9.iloc[-1]) > float(ema21.iloc[-1]) else "DOWN",
            "rsi": "UP" if rsi_val > 50 else "DOWN",
            "macd": "UP" if macd_diff > 0 else "DOWN",
            "bb": "UP" if current > bb_mid else "DOWN",
            "volume": "UP" if vol_cur > vol_avg else "DOWN",
            "momentum": "UP" if price_chg > 0 else "DOWN",
        }

        up = sum(1 for v in signals.values() if v == "UP")
        down = len(signals) - up

        return {
            "signals": signals,
            "prediction": "UP" if up >= down else "DOWN",
            "confidence": round(max(up, down) / len(signals) * 100, 1),
            "up_count": up,
            "down_count": down,
            "rsi_value": rsi_val,
            "ema9": round(float(ema9.iloc[-1]), 4),
            "ema21": round(float(ema21.iloc[-1]), 4),
            "current_price": round(current, 4),
        }
    except Exception as e:
        logger.error(f"Analyze error: {e}")
        return None

def get_btc_trend():
    import requests
    try:
        r = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": "BTCUSDT", "interval": "5m", "limit": 6},
            timeout=10
        )
        closes = [float(d[4]) for d in r.json()]
        chg = (closes[-1] - closes[0]) / closes[0] * 100
        return {
            "price": round(closes[-1], 2),
            "change": round(chg, 2),
            "trend": "UP" if chg > 0 else "DOWN"
        }
    except:
        return None

def get_bnb_price():
    import requests
    try:
        r = requests.get(
            "https://api.binance.com/api/v3/ticker/price",
            params={"symbol": "BNBUSDT"},
            timeout=5
        )
        return round(float(r.json()["price"]), 2)
    except:
        return 0.0

def get_chainlink_price(cl_contract):
    try:
        data = cl_contract.functions.latestRoundData().call()
        return round(data[1] / 1e8, 2)
    except:
        return 0.0

def get_pool_info(pred_contract, epoch):
    try:
        r = pred_contract.functions.rounds(epoch).call()
        total = r[8] / 1e18
        bull = r[9] / 1e18
        bear = r[10] / 1e18
        lock_time = r[2]
        close_time = r[3]
        bull_pct = round(bull / total * 100, 1) if total > 0 else 50.0
        bear_pct = round(bear / total * 100, 1) if total > 0 else 50.0
        bull_pay = round(total / bull, 2) if bull > 0 else 0.0
        bear_pay = round(total / bear, 2) if bear > 0 else 0.0
        return {
            "total": round(total, 4),
            "bull": round(bull, 4),
            "bear": round(bear, 4),
            "bull_pct": bull_pct,
            "bear_pct": bear_pct,
            "bull_payout": bull_pay,
            "bear_payout": bear_pay,
            "lock_time": lock_time,
            "close_time": close_time,
        }
    except Exception as e:
        logger.error(f"Pool error: {e}")
        return None

def do_full_analysis(pred_contract, cl_contract):
    df_1m = get_klines("BNBUSDT", "1m", 100)
    df_5m = get_klines("BNBUSDT", "5m", 50)
    s1 = analyze(df_1m)
    s5 = analyze(df_5m)
    btc = get_btc_trend()
    bnb_price = get_bnb_price()
    cl_price = get_chainlink_price(cl_contract)

    up = (s1["up_count"] if s1 else 3) + (s5["up_count"] if s5 else 3)
    down = (s1["down_count"] if s1 else 3) + (s5["down_count"] if s5 else 3)

    if btc:
        if btc["trend"] == "UP":
            up += 1
        else:
            down += 1

    total = up + down
    prediction = "UP" if up >= down else "DOWN"
    confidence = round(max(up, down) / total * 100, 1) if total > 0 else 50.0

    price_diff = round(abs(cl_price - bnb_price) / bnb_price * 100, 3) if bnb_price > 0 else 0

    return {
        "prediction": prediction,
        "confidence": confidence,
        "s1": s1,
        "s5": s5,
        "btc": btc,
        "bnb_price": bnb_price,
        "cl_price": cl_price,
        "price_diff": price_diff,
    }

def place_bet_tx(w3, pred_contract, user_id, address, epoch, prediction, amount):
    try:
        from web3 import Web3
        pk = get_private_key(user_id)
        if not pk:
            return False, "Không có private key"
        amount_wei = w3.to_wei(amount, "ether")
        nonce = w3.eth.get_transaction_count(Web3.to_checksum_address(address))
        gas_price = w3.to_wei(3, "gwei")
        if prediction == "UP":
            tx = pred_contract.functions.betBull(epoch).build_transaction({
                "from": Web3.to_checksum_address(address),
                "value": amount_wei,
                "gas": 300000,
                "gasPrice": gas_price,
                "nonce": nonce,
            })
        else:
            tx = pred_contract.functions.betBear(epoch).build_transaction({
                "from": Web3.to_checksum_address(address),
                "value": amount_wei,
                "gas": 300000,
                "gasPrice": gas_price,
                "nonce": nonce,
            })
        signed = w3.eth.account.sign_transaction(tx, pk)
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        if receipt.status == 1:
            return True, tx_hash.hex()
        return False, "Transaction thất bại"
    except Exception as e:
        logger.error(f"Bet error: {e}")
        return False, str(e)

def claim_tx(w3, pred_contract, user_id, address, epochs):
    try:
        from web3 import Web3
        pk = get_private_key(user_id)
        nonce = w3.eth.get_transaction_count(Web3.to_checksum_address(address))
        tx = pred_contract.functions.claim(epochs).build_transaction({
            "from": Web3.to_checksum_address(address),
            "gas": 300000,
            "gasPrice": w3.to_wei(3, "gwei"),
            "nonce": nonce,
        })
        signed = w3.eth.account.sign_transaction(tx, pk)
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        return receipt.status == 1, tx_hash.hex()
    except Exception as e:
        logger.error(f"Claim error: {e}")
        return False, str(e)

# ═══════════════════════════════════════
# KEYBOARDS
# ═══════════════════════════════════════
def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑 Cài ví", callback_data="setup_wallet"),
         InlineKeyboardButton("🧪 Test Mode", callback_data="test_mode")],
        [InlineKeyboardButton("▶️ Chạy Bot", callback_data="run_bot"),
         InlineKeyboardButton("📊 Thống kê", callback_data="stats")],
        [InlineKeyboardButton("🏠 Menu", callback_data="back_main")]
    ])

def stop_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛑 DỪNG BOT", callback_data="stop_bot")]
    ])

# ═══════════════════════════════════════
# HANDLERS
# ═══════════════════════════════════════
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    wallet = get_wallet(user_id)

    wallet_text = "❌ Chưa cài ví"
    if wallet:
        try:
            bal = get_bnb_balance(wallet["address"])
            wallet_text = (
                f"✅ Ví: `{wallet['address'][:8]}...{wallet['address'][-6:]}`\n"
                f"💰 Số dư: {bal} BNB"
            )
        except:
            wallet_text = f"✅ Ví: `{wallet['address'][:8]}...{wallet['address'][-6:]}`"

    try:
        await update.message.reply_text(
            f"🤖 *BNB PREDICTION BOT*\n━━━━━━━━━━━━━━━━━━\n\n{wallet_text}",
            parse_mode="Markdown",
            reply_markup=main_keyboard()
        )
    except Exception as e:
        logger.error(f"Start error: {e}")
        await update.message.reply_text("🤖 BNB Bot\n\nGõ /start lại!")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    chat_id = query.message.chat_id
    data = query.data
    session = get_session(user_id)

    try:
        # ── MENU CHÍNH ──
        if data == "back_main":
            wallet = get_wallet(user_id)
            wallet_text = "❌ Chưa cài ví"
            if wallet:
                try:
                    bal = get_bnb_balance(wallet["address"])
                    wallet_text = (
                        f"✅ Ví: `{wallet['address'][:8]}...{wallet['address'][-6:]}`\n"
                        f"💰 Số dư: {bal} BNB"
                    )
                except:
                    wallet_text = f"✅ Ví đã cài"
            await query.edit_message_text(
                f"🤖 *BNB PREDICTION BOT*\n━━━━━━━━━━━━━━━━━━\n\n{wallet_text}",
                parse_mode="Markdown",
                reply_markup=main_keyboard()
            )

        # ── CÀI VÍ ──
        elif data == "setup_wallet":
            await query.edit_message_text(
                "⚠️ *CẢNH BÁO*\n━━━━━━━━━━━━━━━━━━\n\n"
                "🔴 Chỉ dùng ví trade riêng\n"
                "🔴 KHÔNG dùng ví chính\n"
                "🔴 Seed/Key mã hóa AES-256\n"
                "🔴 Bot không chịu trách nhiệm\n\n"
                "Chọn cách nhập:",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🌱 Seed Phrase 12 từ", callback_data="input_seed")],
                    [InlineKeyboardButton("🔐 Private Key", callback_data="input_privkey")],
                    [InlineKeyboardButton("❌ Hủy", callback_data="back_main")]
                ])
            )

        elif data == "input_seed":
            session["waiting_for"] = "seed"
            await query.edit_message_text(
                "🌱 Nhập 12 từ seed phrase, cách nhau bằng dấu cách:\n\n"
                "⚠️ Tin nhắn sẽ bị xóa ngay!"
            )

        elif data == "input_privkey":
            session["waiting_for"] = "privkey"
            await query.edit_message_text(
                "🔐 Nhập private key ví:\n\n"
                "⚠️ Tin nhắn sẽ bị xóa ngay!"
            )

        # ── TEST MODE ──
        elif data == "test_mode":
            await query.edit_message_text(
                "🧪 *TEST MODE*\n━━━━━━━━━━━━━━━━━━\n\n"
                "✅ Chạy thật theo thời gian thực\n"
                "✅ Không dùng tiền thật\n"
                "✅ Vốn ảo: 0.1 BNB\n\n"
                "Chọn số round:",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("10 rounds", callback_data="test_10"),
                     InlineKeyboardButton("20 rounds", callback_data="test_20"),
                     InlineKeyboardButton("50 rounds", callback_data="test_50")],
                    [InlineKeyboardButton("♾️ Liên tục", callback_data="test_0")],
                    [InlineKeyboardButton("❌ Hủy", callback_data="back_main")]
                ])
            )

        elif data.startswith("test_"):
            rounds = int(data.split("_")[1])
            session.update({
                "test_mode": True,
                "balance": 0.1,
                "initial_balance": 0.1,
                "max_rounds": rounds,
                "wins": 0, "losses": 0,
                "total_rounds": 0,
                "profit": 0.0,
                "gas_used": 0.0,
                "running": True,
                "consecutive_losses": 0,
                "current_streak": 0,
                "max_consecutive_wins": 0,
                "max_consecutive_losses": 0,
            })
            rounds_text = f"{rounds} rounds" if rounds > 0 else "Liên tục"
            await query.edit_message_text(
                f"🧪 *TEST MODE BẮT ĐẦU!*\n━━━━━━━━━━━━━━━━━━\n\n"
                f"💰 Vốn ảo: 0.1 BNB\n"
                f"📊 Chế độ: {rounds_text}\n\n"
                f"⏳ Đang chờ phân tích round tiếp theo...",
                parse_mode="Markdown",
                reply_markup=stop_keyboard()
            )
            asyncio.create_task(bot_loop(context, user_id, chat_id))

        # ── CHẠY BOT LIVE ──
        elif data == "run_bot":
            if not get_wallet(user_id):
                await query.edit_message_text(
                    "❌ Chưa cài ví!\n\nDùng Cài ví trước.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔑 Cài ví", callback_data="setup_wallet")],
                        [InlineKeyboardButton("🔙 Quay lại", callback_data="back_main")]
                    ])
                )
                return
            await query.edit_message_text(
                "▶️ *CHỌN SỐ ROUND*",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("♾️ Liên tục", callback_data="rounds_0")],
                    [InlineKeyboardButton("10 rounds", callback_data="rounds_10"),
                     InlineKeyboardButton("20 rounds", callback_data="rounds_20")],
                    [InlineKeyboardButton("50 rounds", callback_data="rounds_50"),
                     InlineKeyboardButton("100 rounds", callback_data="rounds_100")],
                    [InlineKeyboardButton("❌ Hủy", callback_data="back_main")]
                ])
            )

        elif data.startswith("rounds_"):
            session["max_rounds"] = int(data.split("_")[1])
            await query.edit_message_text(
                "🎯 *DỪNG KHI LÃI BAO NHIÊU %?*",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("50%", callback_data="tp_50"),
                     InlineKeyboardButton("100%", callback_data="tp_100"),
                     InlineKeyboardButton("200%", callback_data="tp_200")],
                    [InlineKeyboardButton("⚙️ Tùy chỉnh", callback_data="tp_custom")],
                    [InlineKeyboardButton("❌ Hủy", callback_data="back_main")]
                ])
            )

        elif data.startswith("tp_"):
            val = data.split("_")[1]
            if val == "custom":
                session["waiting_for"] = "stop_profit"
                await query.edit_message_text("Nhập % lãi muốn dừng (VD: 80):")
                return
            session["stop_profit_pct"] = int(val)
            await query.edit_message_text(
                "🛑 *DỪNG KHI LỖ BAO NHIÊU %?*",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("30%", callback_data="sl_30"),
                     InlineKeyboardButton("50%", callback_data="sl_50"),
                     InlineKeyboardButton("70%", callback_data="sl_70")],
                    [InlineKeyboardButton("⚙️ Tùy chỉnh", callback_data="sl_custom")],
                    [InlineKeyboardButton("❌ Hủy", callback_data="back_main")]
                ])
            )

        elif data.startswith("sl_"):
            val = data.split("_")[1]
            if val == "custom":
                session["waiting_for"] = "stop_loss"
                await query.edit_message_text("Nhập % lỗ muốn dừng (VD: 40):")
                return
            session["stop_loss_pct"] = int(val)

            wallet = get_wallet(user_id)
            try:
                bal = get_bnb_balance(wallet["address"])
            except:
                bal = 0.0

            session.update({
                "test_mode": False,
                "balance": bal,
                "initial_balance": bal,
                "wins": 0, "losses": 0,
                "total_rounds": 0,
                "profit": 0.0,
                "gas_used": 0.0,
                "running": True,
                "consecutive_losses": 0,
                "current_streak": 0,
                "max_consecutive_wins": 0,
                "max_consecutive_losses": 0,
            })
            rounds_text = f"{session['max_rounds']} rounds" if session["max_rounds"] > 0 else "Liên tục"
            await query.edit_message_text(
                f"🚀 *BOT BẮT ĐẦU CHẠY!*\n━━━━━━━━━━━━━━━━━━\n\n"
                f"💰 Số dư: {bal} BNB\n"
                f"📊 Chế độ: {rounds_text}\n"
                f"🎯 Dừng lãi: {session['stop_profit_pct']}%\n"
                f"🛑 Dừng lỗ: {session['stop_loss_pct']}%\n\n"
                f"⏳ Đang chờ phân tích round tiếp theo...",
                parse_mode="Markdown",
                reply_markup=stop_keyboard()
            )
            asyncio.create_task(bot_loop(context, user_id, chat_id))

        # ── THỐNG KÊ ──
        elif data == "stats":
            s = get_session(user_id)
            total = s["total_rounds"]
            win_rate = (s["wins"] / total * 100) if total > 0 else 0
            profit_pct = (s["profit"] / s["initial_balance"] * 100) if s["initial_balance"] > 0 else 0
            await query.edit_message_text(
                f"📊 *THỐNG KÊ*\n━━━━━━━━━━━━━━━━━━\n\n"
                f"🔢 Tổng rounds: {total}\n"
                f"✅ Thắng: {s['wins']} ({win_rate:.1f}%)\n"
                f"❌ Thua: {s['losses']}\n"
                f"⛽ Gas: {s['gas_used']:.4f} BNB\n\n"
                f"💰 Vốn đầu: {s['initial_balance']:.4f} BNB\n"
                f"💼 Số dư: {s['balance']:.4f} BNB\n"
                f"📈 Lãi/Lỗ: {s['profit']:+.4f} BNB ({profit_pct:+.1f}%)\n\n"
                f"🔥 Thắng liên tiếp max: {s['max_consecutive_wins']}\n"
                f"💀 Thua liên tiếp max: {s['max_consecutive_losses']}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Menu", callback_data="back_main")]
                ])
            )

        # ── DỪNG BOT ──
        elif data == "stop_bot":
            session["running"] = False
            await query.edit_message_text(
                "🛑 *Bot đã dừng!*\n\nDùng /start để xem thống kê.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🏠 Menu", callback_data="back_main")]
                ])
            )

    except Exception as e:
        logger.error(f"Button handler error: {e}")
        try:
            await query.edit_message_text(
                f"⚠️ Lỗi: {str(e)}\n\nGõ /start lại!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🏠 Menu", callback_data="back_main")]
                ])
            )
        except:
            pass

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session(user_id)
    text = update.message.text.strip()
    waiting = session.get("waiting_for")

    if waiting == "seed":
        try:
            await update.message.delete()
        except:
            pass
        words = text.strip().split()
        if len(words) != 12:
            await context.bot.send_message(chat_id=update.effective_chat.id,
                text="❌ Phải đúng 12 từ! Thử lại /start → Cài ví.")
            return
        wallet = wallet_from_seed(text)
        if wallet:
            init_wallet(user_id, wallet["address"], wallet["private_key"])
            session["waiting_for"] = None
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"✅ Ví đã cài!\n\n📍 `{wallet['address']}`\n\n⚠️ Seed phrase đã mã hóa!",
                parse_mode="Markdown"
            )
        else:
            await context.bot.send_message(chat_id=update.effective_chat.id,
                text="❌ Seed phrase không hợp lệ! Thử lại.")

    elif waiting == "privkey":
        try:
            await update.message.delete()
        except:
            pass
        wallet = wallet_from_privkey(text)
        if wallet:
            init_wallet(user_id, wallet["address"], wallet["private_key"])
            session["waiting_for"] = None
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"✅ Ví đã cài!\n\n📍 `{wallet['address']}`",
                parse_mode="Markdown"
            )
        else:
            await context.bot.send_message(chat_id=update.effective_chat.id,
                text="❌ Private key không hợp lệ! Thử lại.")

    elif waiting == "stop_profit":
        try:
            session["stop_profit_pct"] = int(text)
            session["waiting_for"] = None
            await update.message.reply_text(f"✅ Dừng lãi: {text}%")
        except:
            await update.message.reply_text("❌ Nhập số nguyên! VD: 80")

    elif waiting == "stop_loss":
        try:
            session["stop_loss_pct"] = int(text)
            session["waiting_for"] = None
            await update.message.reply_text(f"✅ Dừng lỗ: {text}%")
        except:
            await update.message.reply_text("❌ Nhập số nguyên! VD: 40")

# ═══════════════════════════════════════
# BOT LOOP CHÍNH
# ═══════════════════════════════════════
async def bot_loop(context, user_id: int, chat_id: int):
    session = get_session(user_id)
    wallet = get_wallet(user_id)

    try:
        w3 = get_w3()
        if not w3:
            await context.bot.send_message(chat_id=chat_id,
                text="❌ Không kết nối được BSC! Thử lại sau.")
            session["running"] = False
            return
        pred_contract, cl_contract = get_contracts(w3)
    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Lỗi kết nối: {e}")
        session["running"] = False
        return

    last_bet_epoch = None
    pending_epoch = None
    pending_prediction = None
    pending_amount = None

    while session["running"]:
        try:
            now = int(time.time())
            epoch = pred_contract.functions.currentEpoch().call()
            pool = get_pool_info(pred_contract, epoch)

            if not pool:
                await asyncio.sleep(10)
                continue

            lock_time = pool["lock_time"]
            close_time = pool["close_time"]
            time_to_lock = lock_time - now

            # ── CLAIM ROUND TRƯỚC ──
            if pending_epoch and pending_epoch < epoch:
                await asyncio.sleep(8)  # chờ oracle cập nhật
                try:
                    round_data = pred_contract.functions.rounds(pending_epoch).call()
                    if round_data[13]:  # oracle called
                        lock_p = round_data[4]
                        close_p = round_data[5]
                        actual = "UP" if close_p > lock_p else "DOWN"
                        won = actual == pending_prediction
                        gas = 0.0005 if not session["test_mode"] else 0.0

                        if not session["test_mode"] and wallet and won:
                            ledger = pred_contract.functions.ledger(
                                pending_epoch,
                                __import__('web3').Web3.to_checksum_address(wallet["address"])
                            ).call()
                            if not ledger[2]:  # not claimed
                                claim_ok, _ = claim_tx(
                                    w3, pred_contract, user_id,
                                    wallet["address"], [pending_epoch]
                                )

                        if won:
                            payout = pool["bull_payout"] if pending_prediction == "UP" else pool["bear_payout"]
                            net = pending_amount * payout - pending_amount - gas
                            session["balance"] += net
                            session["profit"] += net
                            session["wins"] += 1
                            session["consecutive_losses"] = 0
                            session["current_streak"] = max(0, session["current_streak"]) + 1
                            session["max_consecutive_wins"] = max(
                                session["max_consecutive_wins"], session["current_streak"])
                            result_text = f"✅ THẮNG +{net:.4f} BNB"
                        else:
                            loss = pending_amount + gas
                            session["balance"] = max(0, session["balance"] - loss)
                            session["profit"] -= loss
                            session["losses"] += 1
                            session["consecutive_losses"] += 1
                            session["current_streak"] = min(0, session["current_streak"]) - 1
                            session["max_consecutive_losses"] = max(
                                session["max_consecutive_losses"], abs(session["current_streak"]))
                            result_text = f"❌ THUA -{pending_amount + gas:.4f} BNB"

                        session["gas_used"] += gas
                        session["total_rounds"] += 1

                        total = session["total_rounds"]
                        win_rate = session["wins"] / total * 100 if total > 0 else 0
                        profit_pct = session["profit"] / session["initial_balance"] * 100 if session["initial_balance"] > 0 else 0
                        streak = session["current_streak"]
                        streak_text = (f"🔥 Thắng liên tiếp: {streak}"
                                       if streak > 0 else f"💀 Thua liên tiếp: {abs(streak)}")

                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=(
                                f"{'✅' if won else '❌'} *ROUND #{pending_epoch} - {result_text}*\n"
                                f"━━━━━━━━━━━━━━━━━━\n\n"
                                f"🎯 Đặt: {pending_prediction} | Kết quả: {actual}\n"
                                f"💰 Cược: {pending_amount:.4f} BNB\n"
                                f"⛽ Gas: {gas:.4f} BNB\n\n"
                                f"━━ 📊 THỐNG KÊ ━━\n"
                                f"💼 Số dư: {session['balance']:.4f} BNB\n"
                                f"📈 Lãi/Lỗ: {session['profit']:+.4f} BNB ({profit_pct:+.1f}%)\n"
                                f"🎯 Tỉ lệ: {session['wins']}/{total} ({win_rate:.1f}%)\n"
                                f"{streak_text}"
                            ),
                            parse_mode="Markdown",
                            reply_markup=stop_keyboard()
                        )

                        # Kiểm tra điều kiện dừng
                        stop_reason = None
                        if session["balance"] <= 0.005:
                            stop_reason = "💸 Số dư quá thấp!"
                        elif profit_pct >= session["stop_profit_pct"]:
                            stop_reason = f"🎯 Đạt mục tiêu lãi {session['stop_profit_pct']}%!"
                        elif profit_pct <= -session["stop_loss_pct"]:
                            stop_reason = f"🛑 Đạt ngưỡng lỗ {session['stop_loss_pct']}%!"
                        elif session["max_rounds"] > 0 and total >= session["max_rounds"]:
                            stop_reason = f"✅ Hoàn thành {session['max_rounds']} rounds!"

                        if stop_reason:
                            session["running"] = False
                            win_rate = session["wins"] / total * 100 if total > 0 else 0
                            await context.bot.send_message(
                                chat_id=chat_id,
                                text=(
                                    f"🏁 *BOT ĐÃ DỪNG*\n━━━━━━━━━━━━━━━━━━\n\n"
                                    f"Lý do: {stop_reason}\n\n"
                                    f"⏱️ Tổng rounds: {total}\n"
                                    f"✅ Thắng: {session['wins']} ({win_rate:.1f}%)\n"
                                    f"❌ Thua: {session['losses']}\n"
                                    f"⛽ Gas: {session['gas_used']:.4f} BNB\n\n"
                                    f"💰 Vốn đầu: {session['initial_balance']:.4f} BNB\n"
                                    f"💼 Số dư: {session['balance']:.4f} BNB\n"
                                    f"📈 Lãi/Lỗ: {session['profit']:+.4f} BNB ({profit_pct:+.1f}%)\n\n"
                                    f"🔥 Thắng max: {session['max_consecutive_wins']}\n"
                                    f"💀 Thua max: {session['max_consecutive_losses']}"
                                ),
                                parse_mode="Markdown",
                                reply_markup=InlineKeyboardMarkup([
                                    [InlineKeyboardButton("▶️ Chạy tiếp", callback_data="run_bot"),
                                     InlineKeyboardButton("🧪 Test lại", callback_data="test_mode")],
                                    [InlineKeyboardButton("🏠 Menu", callback_data="back_main")]
                                ])
                            )
                            return

                        pending_epoch = None

                except Exception as e:
                    logger.error(f"Claim/result error: {e}")
                    pending_epoch = None

            # ── PHÂN TÍCH & ĐẶT CƯỢC ROUND TIẾP ──
            next_epoch = epoch + 1
            if last_bet_epoch != next_epoch and time_to_lock > 5:
                # Chờ đến 25 giây trước lock
                if time_to_lock > 25:
                    await asyncio.sleep(time_to_lock - 25)

                # Phân tích
                analysis = do_full_analysis(pred_contract, cl_contract)
                prediction = analysis["prediction"]
                confidence = analysis["confidence"]
                s1 = analysis["s1"]
                s5 = analysis["s5"]
                btc = analysis["btc"]

                def se(s, k):
                    if not s:
                        return "❓"
                    return "⬆️" if s["signals"][k] == "UP" else "⬇️"

                btc_text = f"₿ BTC: {btc['change']:+.2f}% {'⬆️' if btc['trend']=='UP' else '⬇️'}" if btc else "₿ BTC: N/A"
                next_pool = get_pool_info(pred_contract, next_epoch)
                pool_text = ""
                if next_pool:
                    pool_text = (
                        f"🐂 Bull: {next_pool['bull_pct']}% (x{next_pool['bull_payout']})\n"
                        f"🐻 Bear: {next_pool['bear_pct']}% (x{next_pool['bear_payout']})"
                    )

                bet_amount = round(session["balance"] * 0.05, 6)
                if not session["test_mode"]:
                    bet_amount = max(round(bet_amount - 0.0005, 6), 0.001)

                mode_text = "🧪 TEST" if session["test_mode"] else "💰 LIVE"
                pred_emoji = "⬆️ UP" if prediction == "UP" else "⬇️ DOWN"

                await context.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"🔮 *PHÂN TÍCH ROUND #{next_epoch}* {mode_text}\n"
                        f"━━━━━━━━━━━━━━━━━━\n\n"
                        f"💵 BNB: ${analysis['bnb_price']}\n"
                        f"⛓️ Chainlink: ${analysis['cl_price']}\n"
                        f"📊 Chênh lệch: {analysis['price_diff']}%\n\n"
                        f"━━ 📈 TÍN HIỆU 1M ━━\n"
                        f"EMA:{se(s1,'ema')} RSI:{se(s1,'rsi')}({s1['rsi_value'] if s1 else '?'}) "
                        f"MACD:{se(s1,'macd')} BB:{se(s1,'bb')} "
                        f"Vol:{se(s1,'volume')} Mom:{se(s1,'momentum')}\n\n"
                        f"━━ 📈 TÍN HIỆU 5M ━━\n"
                        f"EMA:{se(s5,'ema')} RSI:{se(s5,'rsi')}({s5['rsi_value'] if s5 else '?'}) "
                        f"MACD:{se(s5,'macd')} BB:{se(s5,'bb')} "
                        f"Vol:{se(s5,'volume')} Mom:{se(s5,'momentum')}\n\n"
                        f"━━ {btc_text} ━━\n"
                        f"{pool_text}\n\n"
                        f"━━ 🎯 KẾT QUẢ ━━\n"
                        f"Dự đoán: *{pred_emoji}*\n"
                        f"Độ tin cậy: *{confidence}%*\n"
                        f"Cược: *{bet_amount:.4f} BNB*"
                    ),
                    parse_mode="Markdown",
                    reply_markup=stop_keyboard()
                )

                # Đặt cược
                if session["test_mode"]:
                    last_bet_epoch = next_epoch
                    pending_epoch = next_epoch
                    pending_prediction = prediction
                    pending_amount = bet_amount
                else:
                    if wallet:
                        ok, tx = place_bet_tx(
                            w3, pred_contract, user_id,
                            wallet["address"], next_epoch, prediction, bet_amount
                        )
                        if ok:
                            last_bet_epoch = next_epoch
                            pending_epoch = next_epoch
                            pending_prediction = prediction
                            pending_amount = bet_amount
                            await context.bot.send_message(
                                chat_id=chat_id,
                                text=f"✅ Đã đặt {bet_amount:.4f} BNB → {pred_emoji}\n`{tx[:20]}...`",
                                parse_mode="Markdown"
                            )
                        else:
                            await context.bot.send_message(
                                chat_id=chat_id,
                                text=f"❌ Đặt cược thất bại:\n{tx}"
                            )

            # ── CHỜ ROUND HIỆN TẠI KẾT THÚC ──
            now = int(time.time())
            wait = close_time - now + 10
            if wait > 0:
                await asyncio.sleep(min(wait, 30))
            else:
                await asyncio.sleep(10)

        except Exception as e:
            logger.error(f"Bot loop error: {e}")
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ Lỗi vòng lặp: {str(e)}\nThử lại sau 15 giây..."
            )
            await asyncio.sleep(15)

# ═══════════════════════════════════════
# MAIN
# ═══════════════════════════════════════
def main():
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN chưa được cài!")
        return

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    logger.info("✅ BNB Prediction Bot đang chạy...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
