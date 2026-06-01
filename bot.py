import asyncio
import logging
import time
import os
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

# ═══════════════════════════════════════
# GAS / FEE THỰC TẾ TRÊN BSC
# gas = 300_000 × 3 gwei = 900_000 gwei = 0.0009 BNB (worst case)
# Thực tế BSC dùng ít gas hơn, ~150k-200k cho bet, ~200k-250k cho claim
# Để an toàn dùng mức thực đo được:
# ═══════════════════════════════════════
BET_GAS_FEE   = 0.00009    # ~150k gas × 3 gwei thực tế bet
CLAIM_GAS_FEE = 0.00015    # ~250k gas × 3 gwei thực tế claim (cao hơn bet)
BET_AMOUNT    = 0.001

# ═══════════════════════════════════════
# STATE
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
CHAINLINK_ADDRESS  = "0x0567F2323251f0Aab15c8dFb1967E4e8A7D42aeE"

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
    {"inputs":[{"internalType":"uint256","name":"epoch","type":"uint256"},
               {"internalType":"address","name":"user","type":"address"}],
     "name":"claimable",
     "outputs":[{"internalType":"bool","name":"","type":"bool"}],
     "stateMutability":"view","type":"function"},
]

CHAINLINK_ABI = [
    {"inputs":[],"name":"latestRoundData",
     "outputs":[
         {"internalType":"uint80","name":"roundId","type":"uint80"},
         {"internalType":"int256","name":"answer","type":"int256"},
         {"internalType":"uint256","name":"startedAt","type":"uint256"},
         {"internalType":"uint256","name":"updatedAt","type":"uint256"},
         {"internalType":"uint80","name":"answeredInRound","type":"uint80"}
     ],"stateMutability":"view","type":"function"}
]

def get_w3():
    from web3 import Web3
    for rpc in [
        "https://bsc-dataseed1.binance.org/",
        "https://bsc-dataseed2.binance.org/",
        "https://bsc-dataseed3.binance.org/",
    ]:
        try:
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 10}))
            if w3.is_connected(): return w3
        except: continue
    return None

def get_contracts(w3):
    from web3 import Web3
    pred = w3.eth.contract(
        address=Web3.to_checksum_address(PREDICTION_ADDRESS), abi=PREDICTION_ABI)
    cl = w3.eth.contract(
        address=Web3.to_checksum_address(CHAINLINK_ADDRESS), abi=CHAINLINK_ABI)
    return pred, cl

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
            "start_time":    r[1],
            "lock_time":     r[2],
            "close_time":    r[3],
            "lock_price":    r[4],
            "close_price":   r[5],
            "total":         round(total, 4),
            "bull_pct":      round(bull / total * 100, 1) if total > 0 else 50.0,
            "bear_pct":      round(bear / total * 100, 1) if total > 0 else 50.0,
            "bull_payout":   round(total / bull, 2) if bull > 0 else 0.0,
            "bear_payout":   round(total / bear, 2) if bear > 0 else 0.0,
            "reward_amount": r[12],      # wei — pool đã trừ phí 3%
            "oracle_called": r[13],
        }
    except Exception as e:
        logger.error(f"Pool error: {e}")
        return None

def get_claimable_reward(pred, epoch, address):
    """
    Tính toán phần thưởng thực tế người dùng nhận được sau khi claim.
    Công thức: rewardAmount * (bet_amount / rewardBaseCalAmount)
    rewardBaseCalAmount = bullAmount hoặc bearAmount tùy bên thắng.
    Trả về (claimable_bool, reward_wei)
    """
    try:
        from web3 import Web3
        addr = Web3.to_checksum_address(address)
        rd   = pred.functions.rounds(epoch).call()
        ledg = pred.functions.ledger(epoch, addr).call()

        oracle_called  = rd[13]
        lock_price     = rd[4]
        close_price    = rd[5]
        total_amount   = rd[8]
        bull_amount    = rd[9]
        bear_amount    = rd[10]
        reward_base    = rd[11]   # rewardBaseCalAmount (address — unused here, we recalc)
        reward_amount  = rd[12]   # wei pool after 3% fee

        position       = ledg[0]   # 0=Bull, 1=Bear
        bet_amount_wei = ledg[1]
        claimed        = ledg[2]

        if claimed or bet_amount_wei == 0 or not oracle_called:
            return False, 0

        # Xác định bên thắng
        if close_price > lock_price:
            winning_side = 0   # Bull
        elif close_price < lock_price:
            winning_side = 1   # Bear
        else:
            # Hoà → refund
            return (position == 0 or position == 1), bet_amount_wei

        if position != winning_side:
            return False, 0

        # base = tổng tiền bên thắng
        base = bull_amount if winning_side == 0 else bear_amount
        if base == 0:
            return False, 0

        # Reward theo tỷ lệ cược
        user_reward = reward_amount * bet_amount_wei // base
        return True, user_reward

    except Exception as e:
        logger.error(f"get_claimable_reward error: {e}")
        return False, 0

def estimate_gas_cost(w3, gas_used_units, gwei=3):
    """Tính gas thực tế dùng theo wei → BNB"""
    return round(gas_used_units * gwei * 1e9 / 1e18, 8)

def place_bet_tx(w3, pred, user_id, address, epoch, direction, amount):
    try:
        from web3 import Web3
        pk = get_private_key(user_id)
        if not pk: return False, "Không có private key", 0
        addr  = Web3.to_checksum_address(address)
        nonce = w3.eth.get_transaction_count(addr)
        fn = pred.functions.betBull(epoch) if direction == "UP" else pred.functions.betBear(epoch)
        tx = fn.build_transaction({
            "from": addr,
            "value": w3.to_wei(amount, "ether"),
            "gas": 300000,
            "gasPrice": w3.to_wei(3, "gwei"),
            "nonce": nonce,
        })
        signed  = w3.eth.account.sign_transaction(tx, pk)
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        # Gas thực tế dùng
        actual_gas_cost = estimate_gas_cost(w3, receipt.gasUsed)
        return (receipt.status == 1), tx_hash.hex(), actual_gas_cost
    except Exception as e:
        logger.error(f"Bet error: {e}")
        return False, str(e), BET_GAS_FEE

def do_claim_tx(w3, pred, user_id, address, epochs):
    try:
        from web3 import Web3
        pk = get_private_key(user_id)
        addr  = Web3.to_checksum_address(address)
        nonce = w3.eth.get_transaction_count(addr)
        tx = pred.functions.claim(epochs).build_transaction({
            "from": addr, "gas": 300000,
            "gasPrice": w3.to_wei(3, "gwei"), "nonce": nonce,
        })
        signed  = w3.eth.account.sign_transaction(tx, pk)
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        actual_gas_cost = estimate_gas_cost(w3, receipt.gasUsed)
        return receipt.status == 1, tx_hash.hex(), actual_gas_cost
    except Exception as e:
        logger.error(f"Claim error: {e}")
        return False, str(e), CLAIM_GAS_FEE

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
            "signals": signals,
            "prediction": "UP" if up >= down else "DOWN",
            "confidence": round(max(up, down) / len(signals) * 100, 1),
            "up_count": up, "down_count": down,
            "rsi_value": rsi_val,
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
        return {"price": round(closes[-1], 2), "change": round(chg, 2),
                "trend": "UP" if chg > 0 else "DOWN"}
    except: return None

def get_bnb_price():
    import requests
    try:
        r = requests.get("https://api.binance.com/api/v3/ticker/price",
                         params={"symbol": "BNBUSDT"}, timeout=5)
        return round(float(r.json()["price"]), 2)
    except: return 0.0

def get_cl_price(cl):
    try:
        return round(cl.functions.latestRoundData().call()[1] / 1e8, 2)
    except: return 0.0

def full_analysis(pred, cl):
    s1  = analyze(get_klines("BNBUSDT", "1m", 100))
    s5  = analyze(get_klines("BNBUSDT", "5m", 50))
    btc = get_btc_trend()
    bnb = get_bnb_price()
    clp = get_cl_price(cl)
    up   = (s1["up_count"] if s1 else 3) + (s5["up_count"] if s5 else 3)
    down = (s1["down_count"] if s1 else 3) + (s5["down_count"] if s5 else 3)
    if btc:
        if btc["trend"] == "UP": up += 1
        else: down += 1
    total    = up + down
    pred_dir = "UP" if up >= down else "DOWN"
    conf     = round(max(up, down) / total * 100, 1) if total > 0 else 50.0
    diff     = round(abs(clp - bnb) / bnb * 100, 3) if bnb > 0 else 0
    return {"prediction": pred_dir, "confidence": conf,
            "s1": s1, "s5": s5, "btc": btc,
            "bnb_price": bnb, "cl_price": clp, "price_diff": diff}

# ═══════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════
def stop_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛑 DỪNG BOT", callback_data="stop_bot")]
    ])

def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑 Cài ví",    callback_data="setup_wallet"),
         InlineKeyboardButton("🧪 Test Mode", callback_data="test_mode")],
        [InlineKeyboardButton("▶️ Chạy Bot",  callback_data="run_bot"),
         InlineKeyboardButton("📊 Thống kê",  callback_data="stats")],
        [InlineKeyboardButton("💼 Xem số dư", callback_data="check_balance")],
    ])

def se(s, k):
    if not s: return "❓"
    return "⬆️" if s["signals"][k] == "UP" else "⬇️"

def check_stop(session):
    profit_pct = (session["profit"] / session["initial_balance"] * 100
                  ) if session["initial_balance"] > 0 else 0
    if session["balance"] <= 0.002:
        return "💸 Số dư quá thấp!"
    if session["balance"] >= 1.0:
        return "🏆 Đạt mục tiêu 1 BNB!"
    if profit_pct >= session["stop_profit_pct"]:
        return f"🎯 Đạt mục tiêu lãi {session['stop_profit_pct']}%!"
    if profit_pct <= -session["stop_loss_pct"]:
        return f"🛑 Đạt ngưỡng lỗ {session['stop_loss_pct']}%!"
    if session["max_rounds"] > 0 and session["total_rounds"] >= session["max_rounds"]:
        return f"✅ Hoàn thành {session['max_rounds']} rounds!"
    return None

async def send_main(target, user_id, edit=False):
    wallet = get_wallet(user_id)
    wallet_text = "❌ Chưa cài ví"
    if wallet:
        try:
            bal = get_bnb_balance(wallet["address"])
            wallet_text = (f"✅ `{wallet['address'][:8]}...{wallet['address'][-6:]}`\n"
                           f"💰 Số dư: {bal} BNB")
        except:
            wallet_text = f"✅ `{wallet['address'][:8]}...{wallet['address'][-6:]}`"
    text = f"🤖 *BNB PREDICTION BOT*\n━━━━━━━━━━━━━━━━━━\n\n{wallet_text}"
    if edit:
        await target.edit_message_text(text, parse_mode="Markdown", reply_markup=main_kb())
    else:
        await target.reply_text(text, parse_mode="Markdown", reply_markup=main_kb())

async def send_final(context, chat_id, session):
    total      = session["total_rounds"]
    win_rate   = session["wins"] / total * 100 if total > 0 else 0
    profit_pct = session["profit"] / session["initial_balance"] * 100 if session["initial_balance"] > 0 else 0
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"🏁 *KẾT QUẢ PHIÊN*\n━━━━━━━━━━━━━━━━━━\n\n"
            f"⏱️ Tổng rounds: {total}\n"
            f"✅ Thắng: {session['wins']} ({win_rate:.1f}%)\n"
            f"❌ Thua: {session['losses']}\n"
            f"⛽ Gas dùng: {session['gas_used']:.6f} BNB\n\n"
            f"💰 Vốn đầu: {session['initial_balance']:.6f} BNB\n"
            f"💼 Số dư: {session['balance']:.6f} BNB\n"
            f"📈 Lãi/Lỗ: {session['profit']:+.6f} BNB ({profit_pct:+.1f}%)\n\n"
            f"🔥 Thắng liên tiếp max: {session['max_consecutive_wins']}\n"
            f"💀 Thua liên tiếp max: {session['max_consecutive_losses']}"
        ),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("▶️ Chạy tiếp", callback_data="run_bot"),
             Inli
