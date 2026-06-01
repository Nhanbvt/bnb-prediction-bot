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

STRATEGY_LABELS = {
    "ai":      "🤖 AI Analysis",
    "random":  "🎲 Random 50/50",
    "pool":    "💧 Theo Pool",
    "anti_ai": "🔄 Ngược AI",
}

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
            "strategy": "ai",
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
    {"inputs":[{"internalType":"uint256[]","name":"epochs","type":"uint256[]"}],
     "name":"claim","outputs":[],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[],"name":"currentEpoch",
     "outputs":[{"internalType":"uint256","name":"","type":"uint256"}],
     "stateMutability":"view","type":"function"},
    {"inputs":[{"internalType":"uint256","name":"","type":"uint256"}],"name":"rounds",
     "outputs":[
         {"internalType":"uint256","name":"epoch","type":"uint256"},
         {"internalType":"uint256","name":"startTimestamp","type":"uint256"},
         {"internalType":"uint256","name":"lockTimestamp","type":"uint256"},
         {"internalType":"uint256","name":"closeTimestamp","type":"uint256"},
         {"internalType":"int256","name":"lockPrice","type":"int256"},
         {"internalType":"int256","name":"closePrice","type":"int256"},
         {"internalType":"uint256","name":"lockOracleid","type":"uint256"},
         {"internalType":"uint256","name":"closeOracleid","type":"uint256"},
         {"internalType":"uint256","name":"totalAmount","type":"uint256"},
         {"internalType":"uint256","name":"bullAmount","type":"uint256"},
         {"internalType":"uint256","name":"bearAmount","type":"uint256"},
         {"internalType":"address","name":"rewardBaseCalAmount","type":"address"},
         {"internalType":"uint256","name":"rewardAmount","type":"uint256"},
         {"internalType":"bool","name":"oracleCalled","type":"bool"}
     ],"stateMutability":"view","type":"function"},
    {"inputs":[{"internalType":"uint256","name":"epoch","type":"uint256"},
               {"internalType":"address","name":"user","type":"address"}],
     "name":"ledger",
     "outputs":[
         {"internalType":"uint8","name":"position","type":"uint8"},
         {"internalType":"uint256","name":"amount","type":"uint256"},
         {"internalType":"bool","name":"claimed","type":"bool"}
     ],"stateMutability":"view","type":"function"},
]

def get_w3():
    from web3 import Web3
    for rpc in ["https://bsc-dataseed1.binance.org/",
                "https://bsc-dataseed2.binance.org/",
                "https://bsc-dataseed3.binance.org/"]:
        try:
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 10}))
            if w3.is_connected(): return w3
        except: continue
    return None

def get_contracts(w3):
    from web3 import Web3
    return w3.eth.contract(
        address=Web3.to_checksum_address(PREDICTION_ADDRESS),
        abi=PREDICTION_ABI
    )

def get_bnb_balance(address):
    try:
        from web3 import Web3
        w3 = get_w3()
        if not w3: return 0.0
        bal = w3.eth.get_balance(Web3.to_checksum_address(address))
        return round(float(w3.from_wei(bal, "ether")), 6)
    except Exception as e:
        logger.error(f"Balance error: {e}")
        return 0.0

def get_pool_info(pred, epoch):
    try:
        r = pred.functions.rounds(epoch).call()
        total = r[8] / 1e18
        bull  = r[9] / 1e18
        bear  = r[10] / 1e18
        return {
            "start_time":  r[1],
            "lock_time":   r[2],
            "close_time":  r[3],
            "total":       round(total, 4),
            "bull_pct":    round(bull / total * 100, 1) if total > 0 else 50.0,
            "bear_pct":    round(bear / total * 100, 1) if total > 0 else 50.0,
            "bull_payout": round(total / bull, 2) if bull > 0 else 0.0,
            "bear_payout": round(total / bear, 2) if bear > 0 else 0.0,
        }
    except Exception as e:
        logger.error(f"Pool error: {e}")
        return None

def place_bet_tx(w3, pred, user_id, address, epoch, direction, amount):
    try:
        from web3 import Web3
        pk = get_private_key(user_id)
        if not pk: return False, "No key"
        addr  = Web3.to_checksum_address(address)
        nonce = w3.eth.get_transaction_count(addr)
        fn    = pred.functions.betBull(epoch) if direction == "UP" else pred.functions.betBear(epoch)
        tx_base = {
            "from": addr,
            "value": w3.to_wei(amount, "ether"),
            "nonce": nonce,
            "gasPrice": w3.to_wei(1, "gwei"),
        }
        try:
            estimated = w3.eth.estimate_gas(fn.build_transaction(tx_base))
            tx_base["gas"] = int(estimated * 1.1)
        except:
            tx_base["gas"] = 70000
        tx      = fn.build_transaction(tx_base)
        signed  = w3.eth.account.sign_transaction(tx, pk)
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        return (receipt.status == 1), tx_hash.hex()
    except Exception as e:
        logger.error(f"Bet error: {e}")
        return False, str(e)

def do_claim_auto(w3, pred, user_id, address, epochs):
    try:
        from web3 import Web3
        if not epochs: return False, "No epochs"
        pk = get_private_key(user_id)
        if not pk: return False, "No key"
        addr  = Web3.to_checksum_address(address)
        nonce = w3.eth.get_transaction_count(addr)
        tx_base = {
            "from": addr,
            "nonce": nonce,
            "gasPrice": w3.to_wei(1, "gwei"),
        }
        try:
            estimated = w3.eth.estimate_gas(
                pred.functions.claim(epochs).build_transaction(tx_base)
            )
            tx_base["gas"] = int(estimated * 1.1)
        except:
            tx_base["gas"] = 60000
        tx      = pred.functions.claim(epochs).build_transaction(tx_base)
        signed  = w3.eth.account.sign_transaction(tx, pk)
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        return receipt.status == 1, tx_hash.hex()
    except Exception as e:
        logger.error(f"Claim error: {e}")
        return False, str(e)

# ═══════════════════════════════════════
# ANALYSIS
# ═══════════════════════════════════════
def get_klines(symbol="BNBUSDT", interval="1m", limit=100):
    import requests
    try:
        r = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=10
        )
        import pandas as pd
        df = pd.DataFrame(r.json(), columns=[
            "time","open","high","low","close","volume",
            "close_time","quote_vol","trades","taker_buy_base","taker_buy_quote","ignore"
        ])
        for c in ["open","high","low","close","volume"]:
            df[c] = pd.to_numeric(df[c])
        return df
    except Exception as e:
        logger.error(f"Klines error: {e}")
        return None

def analyze(df):
    try:
        from ta.momentum import RSIIndicator
        from ta.trend import MACD, EMAIndicator
        from ta.volatility import BollingerBands
        if df is None or len(df) < 30: return None
        close     = df["close"]
        ema9      = EMAIndicator(close, window=9).ema_indicator()
        ema21     = EMAIndicator(close, window=21).ema_indicator()
        rsi       = RSIIndicator(close, window=14).rsi()
        macd      = MACD(close)
        bb        = BollingerBands(close)
        rsi_val   = round(float(rsi.iloc[-1]), 1)
        macd_diff = float(macd.macd_diff().iloc[-1])
        bb_mid    = float(bb.bollinger_mavg().iloc[-1])
        current   = float(close.iloc[-1])
        vol_avg   = float(df["volume"].tail(10).mean())
        vol_cur   = float(df["volume"].iloc[-1])
        price_chg = (current - float(close.iloc[-6])) / float(close.iloc[-6]) * 100
        signals = {
            "ema":      "UP" if float(ema9.iloc[-1]) > float(ema21.iloc[-1]) else "DOWN",
            "rsi":      "UP" if rsi_val > 50 else "DOWN",
            "macd":     "UP" if macd_diff > 0 else "DOWN",
            "bb":       "UP" if current > bb_mid else "DOWN",
            "volume":   "UP" if vol_cur > vol_avg else "DOWN",
            "momentum": "UP" if price_chg > 0 else "DOWN",
        }
        up   = sum(1 for v in signals.values() if v == "UP")
        down = len(signals) - up
        return {
            "signals":    signals,
            "prediction": "UP" if up >= down else "DOWN",
            "confidence": round(max(up, down) / len(signals) * 100, 1),
            "up_count":   up,
            "down_count": down,
            "rsi_value":  rsi_val,
        }
    except Exception as e:
        logger.error(f"Analyze error: {e}")
        return None

def get_bnb_price():
    import requests
    try:
        r = requests.get("https://api.binance.com/api/v3/ticker/price",
                         params={"symbol": "BNBUSDT"}, timeout=5)
        return round(float(r.json()["price"]), 2)
    except: return 0.0

def full_analysis():
    s1  = analyze(get_klines("BNBUSDT", "1m", 100))
    s5  = analyze(get_klines("BNBUSDT", "5m", 50))
    bnb = get_bnb_price()
    up   = (s1["up_count"] if s1 else 3) + (s5["up_count"] if s5 else 3)
    down = (s1["down_count"] if s1 else 3) + (s5["down_count"] if s5 else 3)
    total    = up + down
    pred_dir = "UP" if up >= down else "DOWN"
    conf     = round(max(up, down) / total * 100, 1) if total > 0 else 50.0
    return {
        "prediction": pred_dir,
        "confidence": conf,
        "s1":  s1,
        "s5":  s5,
        "bnb": bnb,
    }

def pick_direction(strategy, analysis, pool_info):
    if strategy == "random":
        return random.choice(["UP", "DOWN"])
    if strategy == "pool" and pool_info:
        return "UP" if pool_info["bull_payout"] >= pool_info["bear_payout"] else "DOWN"
    if strategy == "anti_ai" and analysis:
        return "DOWN" if analysis["prediction"] == "UP" else "UP"
    # ai (default)
    if analysis:
        return analysis["prediction"]
    return random.choice(["UP", "DOWN"])

def se(s, k):
    if not s: return "?"
    return "UP" if s["signals"][k] == "UP" else "DN"

# ═══════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════
def stop_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("DUNG BOT", callback_data="stop_bot")]])

def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Cai vi",    callback_data="setup_wallet"),
         InlineKeyboardButton("Test Mode", callback_data="test_mode")],
        [InlineKeyboardButton("Chay Bot",  callback_data="run_bot"),
         InlineKeyboardButton("Thong ke",  callback_data="stats")],
        [InlineKeyboardButton("Xem so du", callback_data="check_balance")],
    ])

def strategy_kb(prefix):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("AI Analysis",  callback_data=f"{prefix}ai")],
        [InlineKeyboardButton("Random 50/50", callback_data=f"{prefix}random")],
        [InlineKeyboardButton("Theo Pool",    callback_data=f"{prefix}pool")],
        [InlineKeyboardButton("Nguoc AI",     callback_data=f"{prefix}anti_ai")],
        [InlineKeyboardButton("Huy",          callback_data="back_main")],
    ])

def check_stop(session):
    profit_pct = (session["profit"] / session["initial_balance"] * 100
                  ) if session["initial_balance"] > 0 else 0
    if session["balance"] <= 0.002:
        return "So du qua thap!"
    if session["balance"] >= 1.0:
        return "Dat 1 BNB!"
    if profit_pct >= session["stop_profit_pct"]:
        return f"Dat lai {session['stop_profit_pct']}%!"
    if profit_pct <= -session["stop_loss_pct"]:
        return f"Dat lo {session['stop_loss_pct']}%!"
    if session["max_rounds"] > 0 and session["total_rounds"] >= session["max_rounds"]:
        return f"Hoan thanh {session['max_rounds']} rounds!"
    return None

async def send_main(target, user_id, edit=False):
    wallet = get_wallet(user_id)
    wallet_text = "Chua cai vi"
    if wallet:
        try:
            bal = get_bnb_balance(wallet["address"])
            addr = wallet["address"]
            wallet_text = f"{addr[:8]}...{addr[-6:]}\nSo du: {bal} BNB"
        except:
            addr = wallet["address"]
            wallet_text = f"{addr[:8]}...{addr[-6:]}"
    text = f"BNB PREDICTION BOT\n\n{wallet_text}"
    if edit:
        await target.edit_message_text(text, reply_markup=main_kb())
    else:
        await target.reply_text(text, reply_markup=main_kb())

async def send_final(context, chat_id, session):
    total      = session["total_rounds"]
    win_rate   = session["wins"] / total * 100 if total > 0 else 0
    profit_pct = session["profit"] / session["initial_balance"] * 100 if session["initial_balance"] > 0 else 0
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"KET QUA PHIEN\n\n"
            f"Rounds: {total}\n"
            f"Thang: {session['wins']} ({win_rate:.1f}%)\n"
            f"Thua: {session['losses']}\n\n"
            f"Von: {session['initial_balance']:.6f} BNB\n"
            f"So du: {session['balance']:.6f} BNB\n"
            f"Lai/Lo: {session['profit']:+.6f} BNB ({profit_pct:+.1f}%)\n\n"
            f"Thang lien tiep max: {session['max_consecutive_wins']}\n"
            f"Thua lien tiep max: {session['max_consecutive_losses']}"
        ),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Chay tiep", callback_data="run_bot"),
             InlineKeyboardButton("Test lai",  callback_data="test_mode")],
            [InlineKeyboardButton("Menu",      callback_data="back_main")]
        ])
    )

# ═══════════════════════════════════════
# TELEGRAM HANDLERS
# ═══════════════════════════════════════
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_main(update.message, update.effective_user.id)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    chat_id = query.message.chat_id
    data    = query.data
    session = get_session(user_id)

    try:
        if data == "back_main":
            await send_main(query, user_id, edit=True)

        elif data == "check_balance":
            w = get_wallet(user_id)
            if not w:
                await query.answer("Chua cai vi!", show_alert=True)
                return
            bal = get_bnb_balance(w["address"])
            await query.answer(f"So du: {bal} BNB", show_alert=True)

        elif data == "setup_wallet":
            await query.edit_message_text(
                "CANH BAO\nChi dung vi trade rieng\nSeed ma hoa AES-256\n\nChon cach:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Seed 12 tu",  callback_data="input_seed")],
                    [InlineKeyboardButton("Private Key", callback_data="input_privkey")],
                    [InlineKeyboardButton("Huy",         callback_data="back_main")]
                ])
            )

        elif data == "input_seed":
            session["waiting_for"] = "seed"
            await query.edit_message_text("Nhap 12 tu seed (cach nhau dau cach):")

        elif data == "input_privkey":
            session["waiting_for"] = "privkey"
            await query.edit_message_text("Nhap private key:")

        elif data == "test_mode":
            await query.edit_message_text(
                f"TEST MODE\nVon ao: 0.1 BNB\nMoi lenh: {BET_AMOUNT} BNB\n\nChon so round:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("10",          callback_data="test_10"),
                     InlineKeyboardButton("20",          callback_data="test_20"),
                     InlineKeyboardButton("50",          callback_data="test_50")],
                    [InlineKeyboardButton("Lien tuc",    callback_data="test_0")],
                    [InlineKeyboardButton("Huy",         callback_data="back_main")]
                ])
            )

        elif data.startswith("test_"):
            rounds = int(data.split("_")[1])
            session.update({
                "test_mode": True, "balance": 0.1, "initial_balance": 0.1,
                "max_rounds": rounds, "wins": 0, "losses": 0,
                "total_rounds": 0, "profit": 0.0, "running": False,
                "consecutive_losses": 0, "current_streak": 0,
                "max_consecutive_wins": 0, "max_consecutive_losses": 0,
            })
            await query.edit_message_text(
                "Chon chien luoc dat cuoc:\n\n"
                "AI Analysis - Dung indicator\n"
                "Random 50/50 - Ngau nhien\n"
                "Theo Pool - Ben it tien hon\n"
                "Nguoc AI - Dat nguoc signal",
                reply_markup=strateg
