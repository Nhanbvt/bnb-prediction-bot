
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
    return w3.eth.contract(address=Web3.to_checksum_address(PREDICTION_ADDRESS), abi=PREDICTION_ABI)

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
            "bull_payout": round(total / bull, 2) if bull > 0 else 0.0,
            "bear_payout": round(total / bear, 2) if bear > 0 else 0.0,
        }
    except Exception as e:
        logger.error(f"Pool error: {e}")
        return None

def place_bet_tx(w3, pred, user_id, address, epoch, direction, amount):
    """ĐẶT CƯỢC - GAS TỰ ĐỘNG TÍNH (estimate)"""
    try:
        from web3 import Web3
        pk = get_private_key(user_id)
        if not pk: return False, "No key"
        addr  = Web3.to_checksum_address(address)
        nonce = w3.eth.get_transaction_count(addr)
        
        # Tạo tx chưa ký
        fn = pred.functions.betBull(epoch) if direction == "UP" else pred.functions.betBear(epoch)
        tx_dict = {
            "from": addr,
            "value": w3.to_wei(amount, "ether"),
            "nonce": nonce,
            "gasPrice": w3.to_wei(1, "gwei"),  # 1 gwei như thủ công
        }
        
        # ĐÂY LÀ CHÌA KHÓA: Estimate gas thay vì hardcode
        try:
            estimated_gas = w3.eth.estimate_gas(fn.build_transaction(tx_dict))
            # Cộng 10% buffer để bảo toàn
            gas_limit = int(estimated_gas * 1.1)
        except:
            gas_limit = 70000  # Fallback nếu estimate fail
        
        tx_dict["gas"] = gas_limit
        tx = fn.build_transaction(tx_dict)
        
        signed  = w3.eth.account.sign_transaction(tx, pk)
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        return (receipt.status == 1), tx_hash.hex()
    except Exception as e:
        logger.error(f"Bet error: {e}")
        return False, str(e)

def do_claim_auto(w3, pred, user_id, address, epochs):
    """CLAIM TỰ ĐỘNG - GAS TỰ ĐỘNG TÍNH"""
    try:
        from web3 import Web3
        if not epochs: return False, "No epochs"
        pk = get_private_key(user_id)
        if not pk: return False, "No key"
        addr  = Web3.to_checksum_address(address)
        nonce = w3.eth.get_transaction_count(addr)
        
        tx_dict = {
            "from": addr,
            "nonce": nonce,
            "gasPrice": w3.to_wei(1, "gwei"),  # 1 gwei như thủ công
        }
        
        # ESTIMATE GAS TỰ ĐỘNG
        try:
            estimated_gas = w3.eth.estimate_gas(
                pred.functions.claim(epochs).build_transaction(tx_dict)
            )
            # Cộng 10% buffer
            gas_limit = int(estimated_gas * 1.1)
        except:
            gas_limit = 60000  # Fallback
        
        tx_dict["gas"] = gas_limit
        tx = pred.functions.claim(epochs).build_transaction(tx_dict)
        
        signed  = w3.eth.account.sign_transaction(tx, pk)
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        return receipt.status == 1, tx_hash.hex()
    except Exception as e:
        logger.error(f"Claim error: {e}")
        return False, str(e)

# ═══════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════
def stop_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🛑 DỪNG BOT", callback_data="stop_bot")]])

def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑 Cài ví",    callback_data="setup_wallet"),
         InlineKeyboardButton("🧪 Test Mode", callback_data="test_mode")],
        [InlineKeyboardButton("▶️ Chạy Bot",  callback_data="run_bot"),
         InlineKeyboardButton("📊 Thống kê",  callback_data="stats")],
        [InlineKeyboardButton("💼 Xem số dư", callback_data="check_balance")],
    ])

def check_stop(session):
    profit_pct = (session["profit"] / session["initial_balance"] * 100
                  ) if session["initial_balance"] > 0 else 0
    if session["balance"] <= 0.002:
        return "💸 Số dư quá thấp!"
    if session["balance"] >= 1.0:
        return "🏆 Đạt 1 BNB!"
    if profit_pct >= session["stop_profit_pct"]:
        return f"🎯 Lãi {session['stop_profit_pct']}%!"
    if profit_pct <= -session["stop_loss_pct"]:
        return f"🛑 Lỗ {session['stop_loss_pct']}%!"
    if session["max_rounds"] > 0 and session["total_rounds"] >= session["max_rounds"]:
        return f"✅ {session['max_rounds']} rounds!"
    return None

async def send_main(target, user_id, edit=False):
    wallet = get_wallet(user_id)
    wallet_text = "❌ Chưa cài ví"
    if wallet:
        try:
            bal = get_bnb_balance(wallet["address"])
            wallet_text = f"✅ `{wallet['address'][:8]}...{wallet['address'][-6:]}`\n💰 {bal} BNB"
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
            f"⏱️ Rounds: {total}\n✅ Thắng: {session['wins']} ({win_rate:.1f}%)\n"
            f"❌ Thua: {session['losses']}\n\n"
            f"💰 Vốn: {session['initial_balance']:.6f} BNB\n"
            f"💼 Số dư: {session['balance']:.6f} BNB\n"
            f"📈 Lãi/Lỗ: {session['profit']:+.6f} BNB ({profit_pct:+.1f}%)"
        ),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("▶️ Chạy tiếp", callback_data="run_bot"),
             InlineKeyboardButton("🧪 Test lại",  callback_data="test_mode")],
            [InlineKeyboardButton("🏠 Menu",       callback_data="back_main")]
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
                await query.answer("❌ Chưa cài ví!", show_alert=True)
                return
            await query.answer(f"💰 Số dư: {get_bnb_balance(w['address'])} BNB", show_alert=True)

        elif data == "setup_wallet":
            await query.edit_message_text(
                "⚠️ *CẢNH BÁO*\n🔴 Ví trade riêng\n🔴 Seed mã hóa AES-256\n\nChọn cách:",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🌱 Seed 12 từ", callback_data="input_seed")],
                    [InlineKeyboardButton("🔐 Private Key", callback_data="input_privkey")],
                    [InlineKeyboardButton("❌ Hủy",        callback_data="back_main")]
                ])
            )

        elif data == "input_seed":
            session["waiting_for"] = "seed"
            await query.edit_message_text("🌱 Nhập 12 từ seed (cách nhau dấu cách):")

        elif data == "input_privkey":
            session["waiting_for"] = "privkey"
            await query.edit_message_text("🔐 Nhập private key:")

        elif data == "test_mode":
            await query.edit_message_text(
                f"🧪 *TEST MODE*\n✅ Vốn ảo: 0.1 BNB\n✅ Mỗi lệnh: {BET_AMOUNT} BNB\n\nChọn round:",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("10",  callback_data="test_10"),
                     InlineKeyboardButton("20",  callback_data="test_20"),
                     InlineKeyboardButton("50",  callback_data="test_50")],
                    [InlineKeyboardButton("♾️ Liên tục", callback_data="test_0")],
                    [InlineKeyboardButton("❌ Hủy",      callback_data="back_main")]
                ])
            )

        elif data.startswith("test_"):
            rounds = int(data.split("_")[1])
            session.update({
                "test_mode": True, "balance": 0.1, "initial_balance": 0.1,
                "max_rounds": rounds, "wins": 0, "losses": 0,
                "total_rounds": 0, "profit": 0.0, "running": True,
                "consecutive_losses": 0, "current_streak": 0,
                "max_consecutive_wins": 0, "max_consecutive_losses": 0,
            })
            await query.edit_message_text(
                f"🧪 *TEST!*\n💰 Vốn ảo: 0.1 BNB\n"
                f"📊 Chế độ: {'Liên tục' if rounds == 0 else str(rounds)}\n\n"
                f"⏳ Chờ round...",
                parse_mode="Markdown", reply_markup=stop_kb()
            )
            asyncio.create_task(bot_loop(context, user_id, chat_id))

        elif data == "run_bot":
            if not get_wallet(user_id):
                await query.edit_message_text(
                    "❌ Chưa cài ví!",
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
                    [InlineKeyboardButton("10",          callback_data="rounds_10"),
                     InlineKeyboardButton("20",          callback_data="rounds_20")],
                    [InlineKeyboardButton("50",          callback_data="rounds_50"),
                     InlineKeyboardButton("100",         callback_data="rounds_100")],
                    [InlineKeyboardButton("❌ Hủy",       callback_data="back_main")]
                ])
            )

        elif data.startswith("rounds_"):
            session["max_rounds"] = int(data.split("_")[1])
            await query.edit_message_text(
                "🎯 *DỪNG KHI LÃI %?*",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("50",  callback_data="tp_50"),
                     InlineKeyboardButton("100", callback_data="tp_100"),
                     InlineKeyboardButton("200", callback_data="tp_200")],
                    [InlineKeyboardButton("❌ Hủy", callback_data="back_main")]
                ])
            )

        elif data.startswith("tp_"):
            val = data.split("_")[1]
            session["stop_profit_pct"] = int(val)
            await query.edit_message_text(
                "🛑 *DỪNG KHI LỖ %?*",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("30",  callback_data="sl_30"),
                     InlineKeyboardButton("50",  callback_data="sl_50"),
                     InlineKeyboardButton("70",  callback_data="sl_70")],
                    [InlineKeyboardButton("❌ Hủy", callback_data="back_main")]
                ])
            )

        elif data.startswith("sl_"):
            val = data.split("_")[1]
            session["stop_loss_pct"] = int(val)
            w = get_wallet(user_id)
            bal = get_bnb_balance(w["address"]) if w else 0.0
            session.update({
                "test_mode": False, "balance": bal, "initial_balance": bal,
                "wins": 0, "losses": 0, "total_rounds": 0, "profit": 0.0,
                "running": True, "consecutive_losses": 0, "current_streak": 0,
                "max_consecutive_wins": 0, "max_consecutive_losses": 0,
            })
            rounds_text = "Liên tục" if session["max_rounds"] == 0 else f"{session['max_rounds']}"
            await query.edit_message_text(
                f"🚀 *BẮT ĐẦU!*\n💰 {bal} BNB | 🎯 {session['stop_profit_pct']}% | 🛑 {session['stop_loss_pct']}%\n\n⏳ Chờ...",
                parse_mode="Markdown", reply_markup=stop_kb()
            )
            asyncio.create_task(bot_loop(context, user_id, chat_id))

        elif data == "stats":
            s = get_session(user_id)
            total      = s["total_rounds"]
            win_rate   = s["wins"] / total * 100 if total > 0 else 0
            profit_pct = s["profit"] / s["initial_balance"] * 100 if s["initial_balance"] > 0 else 0
            await query.edit_message_text(
                f"📊 *THỐNG KÊ*\n🔢 {total} | ✅ {s['wins']} ({win_rate:.1f}%) | ❌ {s['losses']}\n\n"
                f"💰 {s['initial_balance']:.6f} → {s['balance']:.6f} BNB\n"
                f"📈 {s['profit']:+.6f} ({profit_pct:+.1f}%)",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menu", callback_data="back_main")]])
            )

        elif data == "stop_bot":
            session["running"] = False
            await query.edit_message_text("🛑 Dừng!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="back_main")]]))

    except Exception as e:
        logger.error(f"Button error: {e}")

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session(user_id)
    text    = update.message.text.strip()
    waiting = session.get("waiting_for")

    if waiting == "seed":
        try: await update.message.delete()
        except: pass
        if len(text.split()) != 12:
            await context.bot.send_message(update.effective_chat.id, "❌ 12 từ!")
            return
        w = wallet_from_seed(text)
        if w:
            init_wallet(user_id, w["address"], w["private_key"])
            session["waiting_for"] = None
            await context.bot.send_message(update.effective_chat.id, f"✅ Ví cài!", parse_mode="Markdown")
        else:
            await context.bot.send_message(update.effective_chat.id, "❌ Seed lỗi!")

    elif waiting == "privkey":
        try: await update.message.delete()
        except: pass
        w = wallet_from_privkey(text)
        if w:
            init_wallet(user_id, w["address"], w["private_key"])
            session["waiting_for"] = None
            await context.bot.send_message(update.effective_chat.id, f"✅ Ví cài!", parse_mode="Markdown")
        else:
            await context.bot.send_message(update.effective_chat.id, "❌ Key lỗi!")

    elif waiting == "stop_profit":
        try:
            session["stop_profit_pct"] = int(text)
            session["waiting_for"] = None
            await update.message.reply_text(f"✅ {text}%")
        except:
            await update.message.reply_text("❌ Số!")

    elif waiting == "stop_loss":
        try:
            session["stop_loss_pct"] = int(text)
            session["waiting_for"] = None
            await update.message.reply_text(f"✅ {text}%")
        except:
            await update.message.reply_text("❌ Số!")

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
        pred = get_contracts(w3)
    except Exception as e:
        await context.bot.send_message(chat_id, f"❌ {e}")
        session["running"] = False
        return

    pending_bets    = {}
    bet_epochs_done = set()
    last_epoch      = None
    to_claim_list   = []

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

            # ── KẾT QUẢ & TỰ ĐỘNG CLAIM ──
            done_epochs = [e for e in list(pending_bets.keys()) if e < epoch]
            for fin in sorted(done_epochs):
                await asyncio.sleep(8)
                try:
                    rd = pred.functions.rounds(fin).call()
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

                    fin_pool = get_pool_info(pred, fin)
                    if won:
                        payout = (fin_pool["bull_payout"] if bet_info["pred"] == "UP"
                                  else fin_pool["bear_payout"]) if fin_pool else 1.9
                        net = round(bet_amt * payout - bet_amt, 6)
                        session["balance"]  = round(session["balance"] + net, 6)
                        session["profit"]   = round(session["profit"] + net, 6)
                        session["wins"]    += 1
                        session["consecutive_losses"] = 0
                        session["current_streak"] = max(0, session["current_streak"]) + 1
                        session["max_consecutive_wins"] = max(
                            session["max_consecutive_wins"], session["current_streak"])
                        result_text = f"✅ +{net:.6f} BNB"
                        
                        if not session["test_mode"]:
                            to_claim_list.append(fin)
                    else:
                        session["balance"]  = round(session["balance"] - bet_amt, 6)
                        session["profit"]   = round(session["profit"] - bet_amt, 6)
                        session["losses"]  += 1
                        session["consecutive_losses"] += 1
                        session["current_streak"] = min(0, session["current_streak"]) - 1
                        session["max_consecutive_losses"] = max(
                            session["max_consecutive_losses"], abs(session["current_streak"]))
                        result_text = f"❌ -{bet_amt:.6f} BNB"

                    session["total_rounds"] += 1
                    del pending_bets[fin]

                    total      = session["total_rounds"]
                    win_rate   = session["wins"] / total * 100 if total > 0 else 0
                    profit_pct = (session["profit"] / session["initial_balance"] * 100
                                  ) if session["initial_balance"] > 0 else 0
                    streak = session["current_streak"]
                    streak_txt = (f"🔥 {streak}"
                                  if streak > 0 else f"💀 {abs(streak)}")

                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=(
                            f"{'✅' if won else '❌'} *ROUND #{fin}*\n"
                            f"🎯 {bet_info['pred']} | {actual}\n"
                            f"💰 {bet_amt:.6f} BNB | {result_text}\n\n"
                            f"💼 {session['balance']:.6f} BNB | 📈 {session['profit']:+.6f} ({profit_pct:+.1f}%)\n"
                            f"🎯 {session['wins']}/{total} ({win_rate:.1f}%) | {streak_txt}"
                        ),
                        parse_mode="Markdown",
                        reply_markup=stop_kb()
                    )

                    # TỰ ĐỘNG CLAIM NGAY
                    if to_claim_list and not session["test_mode"] and wallet:
                        await asyncio.sleep(2)
                        ok, tx = do_claim_auto(w3, pred, user_id, wallet["address"], to)
