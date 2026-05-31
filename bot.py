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

GAS_FEE   = 0.000009
CLAIM_FEE = 0.000009
BET_AMOUNT = 0.001

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
        if not pk: return False, "Không có private key"
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
        return (receipt.status == 1), tx_hash.hex()
    except Exception as e:
        logger.error(f"Bet error: {e}")
        return False, str(e)

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
             InlineKeyboardButton("🧪 Test lại",  callback_data="test_mode")],
            [InlineKeyboardButton("🏠 Menu",       callback_data="back_main")]
        ])
    )

def format_analysis_msg(epoch, analysis, mode_txt, extra=""):
    s1  = analysis["s1"]
    s5  = analysis["s5"]
    btc = analysis["btc"]
    pred_emoji = "⬆️ UP" if analysis["prediction"] == "UP" else "⬇️ DOWN"
    btc_text   = (f"₿ BTC: {btc['change']:+.2f}% {'⬆️' if btc['trend']=='UP' else '⬇️'}"
                  ) if btc else "₿ BTC: N/A"
    return (
        f"🔮 *PHÂN TÍCH ROUND #{epoch}* {mode_txt}{extra}\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"💵 BNB: ${analysis['bnb_price']} | CL: ${analysis['cl_price']}\n"
        f"📊 Chênh: {analysis['price_diff']}%\n\n"
        f"━━ 1M ━━\n"
        f"EMA:{se(s1,'ema')} RSI:{se(s1,'rsi')}({s1['rsi_value'] if s1 else '?'}) "
        f"MACD:{se(s1,'macd')} BB:{se(s1,'bb')} "
        f"Vol:{se(s1,'volume')} Mom:{se(s1,'momentum')}\n\n"
        f"━━ 5M ━━\n"
        f"EMA:{se(s5,'ema')} RSI:{se(s5,'rsi')}({s5['rsi_value'] if s5 else '?'}) "
        f"MACD:{se(s5,'macd')} BB:{se(s5,'bb')} "
        f"Vol:{se(s5,'volume')} Mom:{se(s5,'momentum')}\n\n"
        f"{btc_text}\n\n"
        f"━━ 🎯 KẾT QUẢ ━━\n"
        f"Dự đoán: *{pred_emoji}*\n"
        f"Độ tin cậy: *{analysis['confidence']}%*\n"
        f"Cược: *{BET_AMOUNT} BNB*"
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
            wallet = get_wallet(user_id)
            if not wallet:
                await query.answer("❌ Chưa cài ví!", show_alert=True)
                return
            bal = get_bnb_balance(wallet["address"])
            await query.answer(f"💰 Số dư: {bal} BNB", show_alert=True)

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
                    [InlineKeyboardButton("🔐 Private Key",       callback_data="input_privkey")],
                    [InlineKeyboardButton("❌ Hủy",               callback_data="back_main")]
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

        elif data == "test_mode":
            await query.edit_message_text(
                "🧪 *TEST MODE*\n━━━━━━━━━━━━━━━━━━\n\n"
                "✅ Chạy thật theo thời gian thực\n"
                "✅ Không dùng tiền thật\n"
                "✅ Vốn ảo: 0.1 BNB\n"
                f"✅ Mỗi lệnh: {BET_AMOUNT} BNB\n\n"
                "Chọn số round:",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("10 rounds",   callback_data="test_10"),
                     InlineKeyboardButton("20 rounds",   callback_data="test_20"),
                     InlineKeyboardButton("50 rounds",   callback_data="test_50")],
                    [InlineKeyboardButton("♾️ Liên tục", callback_data="test_0")],
                    [InlineKeyboardButton("❌ Hủy",      callback_data="back_main")]
                ])
            )

        elif data.startswith("test_"):
            rounds = int(data.split("_")[1])
            session.update({
                "test_mode": True, "balance": 0.1, "initial_balance": 0.1,
                "max_rounds": rounds, "wins": 0, "losses": 0,
                "total_rounds": 0, "profit": 0.0, "gas_used": 0.0,
                "running": True, "consecutive_losses": 0, "current_streak": 0,
                "max_consecutive_wins": 0, "max_consecutive_losses": 0,
            })
            await query.edit_message_text(
                f"🧪 *TEST BẮT ĐẦU!*\n━━━━━━━━━━━━━━━━━━\n\n"
                f"💰 Vốn ảo: 0.1 BNB\n"
                f"💸 Mỗi lệnh: {BET_AMOUNT} BNB\n"
                f"📊 Chế độ: {'Liên tục' if rounds == 0 else str(rounds) + ' rounds'}\n\n"
                f"⏳ Đang chờ round mới bắt đầu...",
                parse_mode="Markdown", reply_markup=stop_kb()
            )
            asyncio.create_task(bot_loop(context, user_id, chat_id))

        elif data == "run_bot":
            if not get_wallet(user_id):
                await query.edit_message_text(
                    "❌ Chưa cài ví!\n\nDùng Cài ví trước.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔑 Cài ví",   callback_data="setup_wallet")],
                        [InlineKeyboardButton("🔙 Quay lại", callback_data="back_main")]
                    ])
                )
                return
            await query.edit_message_text(
                "▶️ *CHỌN SỐ ROUND*",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("♾️ Liên tục",  callback_data="rounds_0")],
                    [InlineKeyboardButton("10 rounds",    callback_data="rounds_10"),
                     InlineKeyboardButton("20 rounds",    callback_data="rounds_20")],
                    [InlineKeyboardButton("50 rounds",    callback_data="rounds_50"),
                     InlineKeyboardButton("100 rounds",   callback_data="rounds_100")],
                    [InlineKeyboardButton("❌ Hủy",       callback_data="back_main")]
                ])
            )

        elif data.startswith("rounds_"):
            session["max_rounds"] = int(data.split("_")[1])
            await query.edit_message_text(
                "🎯 *DỪNG KHI LÃI BAO NHIÊU %?*",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("50%",          callback_data="tp_50"),
                     InlineKeyboardButton("100%",         callback_data="tp_100"),
                     InlineKeyboardButton("200%",         callback_data="tp_200")],
                    [InlineKeyboardButton("⚙️ Tùy chỉnh", callback_data="tp_custom")],
                    [InlineKeyboardButton("❌ Hủy",        callback_data="back_main")]
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
                    [InlineKeyboardButton("30%",          callback_data="sl_30"),
                     InlineKeyboardButton("50%",          callback_data="sl_50"),
                     InlineKeyboardButton("70%",          callback_data="sl_70")],
                    [InlineKeyboardButton("⚙️ Tùy chỉnh", callback_data="sl_custom")],
                    [InlineKeyboardButton("❌ Hủy",        callback_data="back_main")]
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
                "test_mode": False, "balance": bal, "initial_balance": bal,
                "wins": 0, "losses": 0, "total_rounds": 0,
                "profit": 0.0, "gas_used": 0.0, "running": True,
                "consecutive_losses": 0, "current_streak": 0,
                "max_consecutive_wins": 0, "max_consecutive_losses": 0,
            })
            rounds_text = "Liên tục" if session["max_rounds"] == 0 else f"{session['max_rounds']} rounds"
            await query.edit_message_text(
                f"🚀 *BOT BẮT ĐẦU!*\n━━━━━━━━━━━━━━━━━━\n\n"
                f"💰 Số dư: {bal} BNB\n"
                f"💸 Mỗi lệnh: {BET_AMOUNT} BNB\n"
                f"📊 Chế độ: {rounds_text}\n"
                f"🎯 Dừng lãi: {session['stop_profit_pct']}%\n"
                f"🛑 Dừng lỗ: {session['stop_loss_pct']}%\n\n"
                f"⏳ Đang chờ round mới bắt đầu...",
                parse_mode="Markdown", reply_markup=stop_kb()
            )
            asyncio.create_task(bot_loop(context, user_id, chat_id))

        elif data == "stats":
            s = get_session(user_id)
            total      = s["total_rounds"]
            win_rate   = s["wins"] / total * 100 if total > 0 else 0
            profit_pct = s["profit"] / s["initial_balance"] * 100 if s["initial_balance"] > 0 else 0
            await query.edit_message_text(
                f"📊 *THỐNG KÊ*\n━━━━━━━━━━━━━━━━━━\n\n"
                f"🔢 Tổng rounds: {total}\n"
                f"✅ Thắng: {s['wins']} ({win_rate:.1f}%)\n"
                f"❌ Thua: {s['losses']}\n"
                f"⛽ Gas: {s['gas_used']:.6f} BNB\n\n"
                f"💰 Vốn đầu: {s['initial_balance']:.6f} BNB\n"
                f"💼 Số dư: {s['balance']:.6f} BNB\n"
                f"📈 Lãi/Lỗ: {s['profit']:+.6f} BNB ({profit_pct:+.1f}%)\n\n"
                f"🔥 Thắng max: {s['max_consecutive_wins']}\n"
                f"💀 Thua max: {s['max_consecutive_losses']}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Menu", callback_data="back_main")]
                ])
            )

        elif data == "stop_bot":
            session["running"] = False
            await query.edit_message_text(
                "🛑 *Bot đã dừng!*\n\nDùng /start để tiếp tục.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🏠 Menu", callback_data="back_main")]
                ])
            )

    except Exception as e:
        logger.error(f"Button error: {e}")
        try:
            await query.edit_message_text(
                f"⚠️ Lỗi: {e}\n\nGõ /start lại!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🏠 Menu", callback_data="back_main")]
                ])
            )
        except: pass

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session(user_id)
    text    = update.message.text.strip()
    waiting = session.get("waiting_for")

    if waiting == "seed":
        try: await update.message.delete()
        except: pass
        if len(text.strip().split()) != 12:
            await context.bot.send_message(update.effective_chat.id,
                "❌ Phải đúng 12 từ! Thử lại.")
            return
        w = wallet_from_seed(text)
        if w:
            init_wallet(user_id, w["address"], w["private_key"])
            session["waiting_for"] = None
            await context.bot.send_message(update.effective_chat.id,
                f"✅ Ví đã cài!\n\n📍 `{w['address']}`\n\n⚠️ Seed đã mã hóa!",
                parse_mode="Markdown")
        else:
            await context.bot.send_message(update.effective_chat.id,
                "❌ Seed phrase không hợp lệ!")

    elif waiting == "privkey":
        try: await update.message.delete()
        except: pass
        w = wallet_from_privkey(text)
        if w:
            init_wallet(user_id, w["address"], w["private_key"])
            session["waiting_for"] = None
            await context.bot.send_message(update.effective_chat.id,
                f"✅ Ví đã cài!\n\n📍 `{w['address']}`",
                parse_mode="Markdown")
        else:
            await context.bot.send_message(update.effective_chat.id,
                "❌ Private key không hợp lệ!")

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
# BOT LOOP
# ═══════════════════════════════════════
async def bot_loop(context, user_id: int, chat_id: int):
    session = get_session(user_id)
    wallet  = get_wallet(user_id)

    try:
        w3 = get_w3()
        if not w3:
            await context.bot.send_message(chat_id, "❌ Không kết nối BSC!")
            session["running"] = False
            return
        pred, cl = get_contracts(w3)
    except Exception as e:
        await context.bot.send_message(chat_id, f"❌ Lỗi kết nối: {e}")
        session["running"] = False
        return

    pending_bets    = {}   # {epoch: {pred, amount}}
    bet_epochs_done = set()
    last_epoch      = None

    while session["running"]:
        try:
            now   = int(time.time())
            epoch = pred.functions.currentEpoch().call()
            pool  = get_pool_info(pred, epoch)

            if not pool:
                await asyncio.sleep(10)
                continue

            start_time = pool["start_time"]
            lock_time  = pool["lock_time"]
            close_time = pool["close_time"]
            time_to_lock  = lock_time - now
            time_from_start = now - start_time

            # ── CLAIM / KẾT QUẢ ROUND ĐÃ QUA ──
            done_epochs = [e for e in list(pending_bets.keys()) if e < epoch]
            for fin in sorted(done_epochs):
                await asyncio.sleep(8)
                try:
                    rd = pred.functions.rounds(fin).call()
                    # Chờ oracle nếu chưa cập nhật
                    retry = 0
                    while not rd[13] and retry < 6:
                        await asyncio.sleep(5)
                        rd = pred.functions.rounds(fin).call()
                        retry += 1

                    if not rd[13]:
                        del pending_bets[fin]
                        continue

                    bet_info = pending_bets[fin]
                    actual   = "UP" if rd[5] > rd[4] else "DOWN"
                    won      = actual == bet_info["pred"]
                    bet_amt  = bet_info["amount"]
                    gas      = GAS_FEE if not session["test_mode"] else 0.0

                    # Claim nếu thắng live
                    claim_gas = 0.0
                    if won and not session["test_mode"] and wallet:
                        try:
                            from web3 import Web3
                            ledger = pred.functions.ledger(
                                fin, Web3.to_checksum_address(wallet["address"])
                            ).call()
                            if not ledger[2]:
                                ok, _ = do_claim_tx(w3, pred, user_id, wallet["address"], [fin])
                                if ok:
                                    claim_gas = CLAIM_FEE
                        except Exception as ce:
                            logger.error(f"Claim error: {ce}")

                    # Tính payout
                    fin_pool = get_pool_info(pred, fin)
                    if won:
                        payout = (fin_pool["bull_payout"] if bet_info["pred"] == "UP"
                                  else fin_pool["bear_payout"]) if fin_pool else 1.9
                        net = round(bet_amt * payout - bet_amt - gas - claim_gas, 6)
                        session["balance"]  = round(session["balance"] + net, 6)
                        session["profit"]   = round(session["profit"] + net, 6)
                        session["wins"]    += 1
                        session["consecutive_losses"] = 0
                        session["current_streak"] = max(0, session["current_streak"]) + 1
                        session["max_consecutive_wins"] = max(
                            session["max_consecutive_wins"], session["current_streak"])
                        result_text = f"✅ THẮNG +{net:.6f} BNB"
                    else:
                        loss = round(bet_amt + gas, 6)
                        session["balance"]  = round(max(0, session["balance"] - loss), 6)
                        session["profit"]   = round(session["profit"] - loss, 6)
                        session["losses"]  += 1
                        session["consecutive_losses"] += 1
                        session["current_streak"] = min(0, session["current_streak"]) - 1
                        session["max_consecutive_losses"] = max(
                            session["max_consecutive_losses"], abs(session["current_streak"]))
                        result_text = f"❌ THUA -{loss:.6f} BNB"

                    total_gas = gas + claim_gas
                    session["gas_used"]     = round(session["gas_used"] + total_gas, 6)
                    session["total_rounds"] += 1
                    del pending_bets[fin]

                    total      = session["total_rounds"]
                    win_rate   = session["wins"] / total * 100 if total > 0 else 0
                    profit_pct = (session["profit"] / session["initial_balance"] * 100
                                  ) if session["initial_balance"] > 0 else 0
                    streak     = session["current_streak"]
                    streak_txt = (f"🔥 Thắng liên tiếp: {streak}"
                                  if streak > 0 else f"💀 Thua liên tiếp: {abs(streak)}")

                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=(
                            f"{'✅' if won else '❌'} *ROUND #{fin}*\n"
                            f"━━━━━━━━━━━━━━━━━━\n\n"
                            f"🎯 Đặt: {bet_info['pred']} | Kết quả: {actual}\n"
                            f"💰 Cược: {bet_amt:.6f} BNB\n"
                            f"⛽ Gas: {total_gas:.6f} BNB\n"
                            f"{result_text}\n\n"
                            f"━━ 📊 TỔNG KẾT ━━\n"
                            f"💼 Số dư: {session['balance']:.6f} BNB\n"
                            f"📈 Lãi/Lỗ: {session['profit']:+.6f} BNB ({profit_pct:+.1f}%)\n"
                            f"🎯 Tỉ lệ: {session['wins']}/{total} ({win_rate:.1f}%)\n"
                            f"{streak_txt}"
                        ),
                        parse_mode="Markdown",
                        reply_markup=stop_kb()
                    )

                    # Kiểm tra dừng
                    stop_reason = check_stop(session)
                    if stop_reason:
                        session["running"] = False
                        await context.bot.send_message(chat_id, f"🏁 {stop_reason}")
                        await send_final(context, chat_id, session)
                        return

                except Exception as e:
                    logger.error(f"Result error epoch {fin}: {e}")
                    if fin in pending_bets:
                        del pending_bets[fin]

            # ── ĐẶT CƯỢC: CHỈ KHI ROUND MỚI BẮT ĐẦU (30 giây đầu) ──
            is_new_round   = (epoch != last_epoch)
            in_bet_window  = (time_from_start <= 30 and time_to_lock > 10)

            if is_new_round and in_bet_window and epoch not in bet_epochs_done:
                last_epoch = epoch

                # Đếm ngược hiển thị
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"⏳ *ROUND #{epoch} BẮT ĐẦU!*\n"
                        f"━━━━━━━━━━━━━━━━━━\n\n"
                        f"🔍 Đang phân tích...\n"
                        f"⏱️ Còn {time_to_lock}s để đặt lệnh"
                    ),
                    parse_mode="Markdown"
                )

                # Phân tích
                analysis  = full_analysis(pred, cl)
                direction = analysis["prediction"]
                ep_pool   = get_pool_info(pred, epoch)
                pool_text = ""
                if ep_pool:
                    pool_text = (
                        f"\n🐂 Bull: {ep_pool['bull_pct']}% (x{ep_pool['bull_payout']})\n"
                        f"🐻 Bear: {ep_pool['bear_pct']}% (x{ep_pool['bear_payout']})"
                    )

                mode_txt = "🧪 TEST" if session["test_mode"] else "💰 LIVE"
                msg = format_analysis_msg(epoch, analysis, mode_txt) + pool_text

                await context.bot.send_message(
                    chat_id=chat_id,
                    text=msg,
                    parse_mode="Markdown",
                    reply_markup=stop_kb()
                )

                # Đặt cược
                if session["test_mode"]:
                    pending_bets[epoch] = {"pred": direction, "amount": BET_AMOUNT}
                    bet_epochs_done.add(epoch)
                    pred_emoji = "⬆️ UP" if direction == "UP" else "⬇️ DOWN"
                    await context.bot.send_message(
                        chat_id,
                        f"📝 *TEST* Đặt {BET_AMOUNT} BNB → {pred_emoji}",
                        parse_mode="Markdown"
                    )
                else:
                    if wallet:
                        ok, tx = place_bet_tx(
                            w3, pred, user_id,
                            wallet["address"], epoch, direction, BET_AMOUNT
                        )
                        pred_emoji = "⬆️ UP" if direction == "UP" else "⬇️ DOWN"
                        if ok:
                            pending_bets[epoch] = {"pred": direction, "amount": BET_AMOUNT}
                            bet_epochs_done.add(epoch)
                            await context.bot.send_message(
                                chat_id,
                                f"✅ Đặt {BET_AMOUNT} BNB → {pred_emoji}\n`{tx[:20]}...`",
                                parse_mode="Markdown"
                            )
                        else:
                            await context.bot.send_message(
                                chat_id, f"❌ Đặt cược thất bại:\n{tx}")

            # ── CHỜ ──
            now  = int(time.time())
            # Nếu đang trong round và chưa đặt → chờ đến round mới
            if epoch in bet_epochs_done:
                wait = close_time - now + 5
                await asyncio.sleep(max(min(wait, 30), 5))
            else:
                # Chưa đặt → chờ ít để check lại
                await asyncio.sleep(5)

        except Exception as e:
            logger.error(f"Loop error: {e}")
            await context.bot.send_message(
                chat_id, f"⚠️ Lỗi: {e}\nThử lại sau 15s...")
            await asyncio.sleep(15)

# ═══════════════════════════════════════
# MAIN
# ═══════════════════════════════════════
def main():
    if not TELEGRAM_TOKEN:
        logger.error("❌ TELEGRAM_TOKEN chưa cài!")
        return
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    logger.info("✅ BNB Prediction Bot đang chạy...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
