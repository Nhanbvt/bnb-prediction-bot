import asyncio
import logging
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)
from config import TELEGRAM_TOKEN
from wallet import (
    wallet_from_seed, wallet_from_private_key,
    validate_seed_phrase, save_wallet, get_wallet
)
from predictor import (
    get_w3, get_contracts, get_balance,
    get_current_epoch, get_round_times,
    check_round_result, place_bet, claim_winnings,
    full_analysis
)
import json

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# State lưu trữ
bot_sessions = {}

def get_session(user_id):
    if user_id not in bot_sessions:
        bot_sessions[user_id] = {
            "running": False,
            "test_mode": False,
            "balance": 0,
            "initial_balance": 0,
            "wins": 0,
            "losses": 0,
            "total_rounds": 0,
            "profit": 0,
            "consecutive_losses": 0,
            "max_consecutive_wins": 0,
            "max_consecutive_losses": 0,
            "current_streak": 0,
            "stop_profit_pct": 100,
            "stop_loss_pct": 50,
            "max_rounds": 0,
            "gas_used": 0,
            "pending_claim": [],
            "waiting_for": None,
            "custom_rounds": False,
        }
    return bot_sessions[user_id]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    wallet = get_wallet(user_id)

    if wallet:
        w3 = get_w3()
        balance = get_balance(w3, wallet["address"])
        wallet_text = f"✅ Ví: `{wallet['address'][:8]}...{wallet['address'][-6:]}`\n💰 Số dư: {balance} BNB"
    else:
        wallet_text = "❌ Chưa cài ví"

    msg = f"""
🤖 *BNB PREDICTION BOT*
━━━━━━━━━━━━━━━━━━

{wallet_text}

📌 *Menu chính:*
"""
    keyboard = [
        [InlineKeyboardButton("🔑 Cài ví", callback_data="setup_wallet"),
         InlineKeyboardButton("🧪 Test Mode", callback_data="test_mode")],
        [InlineKeyboardButton("▶️ Chạy Bot", callback_data="run_bot"),
         InlineKeyboardButton("📊 Thống kê", callback_data="stats")],
        [InlineKeyboardButton("⚙️ Cài đặt", callback_data="settings"),
         InlineKeyboardButton("❓ Hướng dẫn", callback_data="help")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    session = get_session(user_id)

    # === SETUP WALLET ===
    if data == "setup_wallet":
        keyboard = [
            [InlineKeyboardButton("🌱 Nhập Seed Phrase 12 từ", callback_data="input_seed")],
            [InlineKeyboardButton("🔐 Nhập Private Key", callback_data="input_privkey")],
            [InlineKeyboardButton("❌ Hủy", callback_data="cancel")]
        ]
        msg = """
⚠️ *CẢNH BÁO QUAN TRỌNG*
━━━━━━━━━━━━━━━━━━

🔴 CHỈ dùng ví trade riêng
🔴 KHÔNG dùng ví chính/ví lạnh
🔴 Chỉ nạp số tiền chấp nhận mất
🔴 Seed/Key được mã hóa AES-256
🔴 Bot không chịu trách nhiệm mất tiền

Chọn cách nhập ví:
"""
        await query.edit_message_text(msg, parse_mode="Markdown",
                                       reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "input_seed":
        session["waiting_for"] = "seed"
        await query.edit_message_text(
            "🌱 Nhập 12 từ seed phrase (cách nhau bằng dấu cách):\n\n"
            "⚠️ Bot sẽ xóa tin nhắn ngay sau khi nhận!",
            parse_mode="Markdown"
        )

    elif data == "input_privkey":
        session["waiting_for"] = "privkey"
        await query.edit_message_text(
            "🔐 Nhập private key ví của bạn:\n\n"
            "⚠️ Bot sẽ xóa tin nhắn ngay sau khi nhận!",
            parse_mode="Markdown"
        )

    # === TEST MODE ===
    elif data == "test_mode":
        keyboard = [
            [InlineKeyboardButton("10 rounds", callback_data="test_10"),
             InlineKeyboardButton("20 rounds", callback_data="test_20"),
             InlineKeyboardButton("50 rounds", callback_data="test_50")],
            [InlineKeyboardButton("❌ Hủy", callback_data="cancel")]
        ]
        await query.edit_message_text(
            "🧪 *CHẾ ĐỘ TEST*\n━━━━━━━━━━━━━━━━━━\n\n"
            "Chạy thật theo thời gian thực\n"
            "✅ Không dùng tiền thật\n"
            "✅ Vốn ảo: 0.1 BNB\n\n"
            "Chọn số round test:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("test_"):
        rounds = int(data.split("_")[1])
        session["test_mode"] = True
        session["balance"] = 0.1
        session["initial_balance"] = 0.1
        session["max_rounds"] = rounds
        session["wins"] = 0
        session["losses"] = 0
        session["total_rounds"] = 0
        session["profit"] = 0
        session["gas_used"] = 0
        session["running"] = True
        await query.edit_message_text(
            f"🧪 Test mode bắt đầu! {rounds} rounds\nVốn ảo: 0.1 BNB",
            parse_mode="Markdown"
        )
        asyncio.create_task(run_bot_loop(context, user_id, query.message.chat_id))

    # === RUN BOT ===
    elif data == "run_bot":
        wallet = get_wallet(user_id)
        if not wallet:
            await query.edit_message_text(
                "❌ Chưa cài ví!\nDùng /start → Cài ví trước."
            )
            return

        keyboard = [
            [InlineKeyboardButton("♾️ Chạy liên tục", callback_data="rounds_0")],
            [InlineKeyboardButton("10 rounds", callback_data="rounds_10"),
             InlineKeyboardButton("20 rounds", callback_data="rounds_20")],
            [InlineKeyboardButton("50 rounds", callback_data="rounds_50"),
             InlineKeyboardButton("⚙️ Tùy chỉnh", callback_data="rounds_custom")],
            [InlineKeyboardButton("❌ Hủy", callback_data="cancel")]
        ]
        await query.edit_message_text(
            "▶️ *CHỌN CHẾ ĐỘ CHẠY*\n━━━━━━━━━━━━━━━━━━\n\nChạy bao nhiêu round?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("rounds_"):
        val = data.split("_")[1]
        if val == "custom":
            session["waiting_for"] = "custom_rounds"
            session["custom_rounds"] = True
            await query.edit_message_text("Nhập số round muốn chạy:")
            return

        rounds = int(val)
        session["max_rounds"] = rounds
        session["test_mode"] = False

        keyboard = [
            [InlineKeyboardButton("Lãi 50%", callback_data="tp_50"),
             InlineKeyboardButton("Lãi 100%", callback_data="tp_100"),
             InlineKeyboardButton("Lãi 200%", callback_data="tp_200")],
            [InlineKeyboardButton("⚙️ Tùy chỉnh", callback_data="tp_custom")],
        ]
        await query.edit_message_text(
            "🎯 *DỪNG KHI LÃI BAO NHIÊU %?*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("tp_"):
        val = data.split("_")[1]
        if val == "custom":
            session["waiting_for"] = "stop_profit"
            await query.edit_message_text("Nhập % lãi muốn dừng (VD: 80):")
            return
        session["stop_profit_pct"] = int(val)

        keyboard = [
            [InlineKeyboardButton("Lỗ 30%", callback_data="sl_30"),
             InlineKeyboardButton("Lỗ 50%", callback_data="sl_50"),
             InlineKeyboardButton("Lỗ 70%", callback_data="sl_70")],
            [InlineKeyboardButton("⚙️ Tùy chỉnh", callback_data="sl_custom")],
        ]
        await query.edit_message_text(
            "🛑 *DỪNG KHI LỖ BAO NHIÊU %?*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("sl_"):
        val = data.split("_")[1]
        if val == "custom":
            session["waiting_for"] = "stop_loss"
            await query.edit_message_text("Nhập % lỗ muốn dừng (VD: 40):")
            return
        session["stop_loss_pct"] = int(val)

        # Bắt đầu chạy
        wallet = get_wallet(user_id)
        w3 = get_w3()
        balance = get_balance(w3, wallet["address"])
        session["balance"] = balance
        session["initial_balance"] = balance
        session["wins"] = 0
        session["losses"] = 0
        session["total_rounds"] = 0
        session["profit"] = 0
        session["gas_used"] = 0
        session["running"] = True

        rounds_text = f"{session['max_rounds']} rounds" if session["max_rounds"] > 0 else "Liên tục"
        await query.edit_message_text(
            f"🚀 *BOT BẮT ĐẦU CHẠY!*\n━━━━━━━━━━━━━━━━━━\n\n"
            f"💰 Số dư: {balance} BNB\n"
            f"📊 Chế độ: {rounds_text}\n"
            f"🎯 Dừng lãi: {session['stop_profit_pct']}%\n"
            f"🛑 Dừng lỗ: {session['stop_loss_pct']}%",
            parse_mode="Markdown"
        )
        asyncio.create_task(run_bot_loop(context, user_id, query.message.chat_id))

    # === STATS ===
    elif data == "stats":
        session = get_session(user_id)
        win_rate = (session["wins"] / session["total_rounds"] * 100) if session["total_rounds"] > 0 else 0
        profit_pct = (session["profit"] / session["initial_balance"] * 100) if session["initial_balance"] > 0 else 0

        msg = f"""
📊 *THỐNG KÊ*
━━━━━━━━━━━━━━━━━━

🔢 Tổng rounds: {session['total_rounds']}
✅ Thắng: {session['wins']} ({win_rate:.1f}%)
❌ Thua: {session['losses']}
⛽ Gas đã dùng: {session['gas_used']:.4f} BNB

💰 Vốn ban đầu: {session['initial_balance']:.4f} BNB
💼 Số dư hiện tại: {session['balance']:.4f} BNB
📈 Lãi/Lỗ: {session['profit']:+.4f} BNB ({profit_pct:+.1f}%)

🔥 Thắng liên tiếp max: {session['max_consecutive_wins']}
💀 Thua liên tiếp max: {session['max_consecutive_losses']}
"""
        keyboard = [[InlineKeyboardButton("🔙 Menu", callback_data="back_main")]]
        await query.edit_message_text(msg, parse_mode="Markdown",
                                       reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "cancel" or data == "back_main":
        await start_from_callback(query, context, user_id)

    elif data == "stop_bot":
        session["running"] = False
        await query.edit_message_text("🛑 Bot đã dừng!\n\nDùng /start để xem thống kê.")

async def start_from_callback(query, context, user_id):
    wallet = get_wallet(user_id)
    if wallet:
        w3 = get_w3()
        balance = get_balance(w3, wallet["address"])
        wallet_text = f"✅ Ví: `{wallet['address'][:8]}...{wallet['address'][-6:]}`\n💰 Số dư: {balance} BNB"
    else:
        wallet_text = "❌ Chưa cài ví"

    msg = f"🤖 *BNB PREDICTION BOT*\n━━━━━━━━━━━━━━━━━━\n\n{wallet_text}"
    keyboard = [
        [InlineKeyboardButton("🔑 Cài ví", callback_data="setup_wallet"),
         InlineKeyboardButton("🧪 Test Mode", callback_data="test_mode")],
        [InlineKeyboardButton("▶️ Chạy Bot", callback_data="run_bot"),
         InlineKeyboardButton("📊 Thống kê", callback_data="stats")],
        [InlineKeyboardButton("⚙️ Cài đặt", callback_data="settings"),
         InlineKeyboardButton("❓ Hướng dẫn", callback_data="help")]
    ]
    await query.edit_message_text(msg, parse_mode="Markdown",
                                   reply_markup=InlineKeyboardMarkup(keyboard))

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
        if validate_seed_phrase(text):
            wallet = wallet_from_seed(text)
            if wallet:
                save_wallet(user_id, wallet)
                session["waiting_for"] = None
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"✅ Ví đã cài thành công!\n\n"
                         f"📍 Address: `{wallet['address']}`\n\n"
                         f"⚠️ Seed phrase đã được mã hóa và lưu an toàn!",
                    parse_mode="Markdown"
                )
            else:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="❌ Seed phrase không hợp lệ! Thử lại."
                )
        else:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Phải đúng 12 từ! Thử lại."
            )

    elif waiting == "privkey":
        try:
            await update.message.delete()
        except:
            pass
        wallet = wallet_from_private_key(text)
        if wallet:
            save_wallet(user_id, wallet)
            session["waiting_for"] = None
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"✅ Ví đã cài thành công!\n\n"
                     f"📍 Address: `{wallet['address']}`",
                parse_mode="Markdown"
            )
        else:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Private key không hợp lệ! Thử lại."
            )

    elif waiting == "stop_profit":
        try:
            val = int(text)
            session["stop_profit_pct"] = val
            session["waiting_for"] = None
            await update.message.reply_text(f"✅ Dừng lãi: {val}%")
        except:
            await update.message.reply_text("❌ Nhập số nguyên! VD: 80")

    elif waiting == "stop_loss":
        try:
            val = int(text)
            session["stop_loss_pct"] = val
            session["waiting_for"] = None
            await update.message.reply_text(f"✅ Dừng lỗ: {val}%")
        except:
            await update.message.reply_text("❌ Nhập số nguyên! VD: 40")

    elif waiting == "custom_rounds":
        try:
            val = int(text)
            session["max_rounds"] = val
            session["waiting_for"] = None
            await update.message.reply_text(f"✅ Số rounds: {val}")
        except:
            await update.message.reply_text("❌ Nhập số nguyên! VD: 30")

async def run_bot_loop(context, user_id: int, chat_id: int):
    session = get_session(user_id)
    w3 = get_w3()
    prediction_contract, chainlink_contract = get_contracts(w3)
    wallet = get_wallet(user_id)

    while session["running"]:
        try:
            epoch = get_current_epoch(prediction_contract)
            round_info = get_round_times(prediction_contract, epoch)
            now = int(time.time())
            lock_time = round_info["lock"]
            time_to_lock = lock_time - now

            # Chờ đến 30 giây trước khi lock
            if time_to_lock > 30:
                await asyncio.sleep(time_to_lock - 30)

            # Phân tích
            analysis = full_analysis(w3, prediction_contract, chainlink_contract)
            prediction = analysis["prediction"]
            confidence = analysis["confidence"]
            pool = analysis["pool"]
            btc = analysis["btc"]
            s1 = analysis["signals_1m"]
            s5 = analysis["signals_5m"]

            # Tính cược
            bet_amount = round(session["balance"] * 0.05, 6)
            gas_cost = 0.0005

            # Format thông báo phân tích
            def sig_emoji(s, key):
                if not s:
                    return "❓"
                return "⬆️" if s["signals"][key] == "UP" else "⬇️"

            btc_text = ""
            if btc:
                btc_text = f"₿ BTC 5m: {btc['change_5m']:+.2f}% {'⬆️' if btc['trend'] == 'UP' else '⬇️'}"

            pool_text = ""
            if pool:
                pool_text = (f"🐂 Bull: {pool['bull_pct']}% (x{pool['bull_payout']})\n"
                             f"🐻 Bear: {pool['bear_pct']}% (x{pool['bear_payout']})")

            pred_emoji = "⬆️ UP" if prediction == "UP" else "⬇️ DOWN"
            mode_text = "🧪 TEST" if session["test_mode"] else "💰 LIVE"

            analysis_msg = f"""
🔮 *PHÂN TÍCH ROUND #{epoch}* {mode_text}
━━━━━━━━━━━━━━━━━━

💵 BNB: ${analysis['binance_price']}
⛓️ Chainlink: ${analysis['chainlink_price']}
📊 Chênh lệch: {analysis['price_diff']}%

━━ 📈 TÍN HIỆU 1M ━━
EMA: {sig_emoji(s1,'ema')} | RSI: {sig_emoji(s1,'rsi')} ({s1['rsi_value'] if s1 else '?'})
MACD: {sig_emoji(s1,'macd')} | BB: {sig_emoji(s1,'bb')}
Vol: {sig_emoji(s1,'volume')} | Mom: {sig_emoji(s1,'momentum')}

━━ 📈 TÍN HIỆU 5M ━━
EMA: {sig_emoji(s5,'ema')} | RSI: {sig_emoji(s5,'rsi')} ({s5['rsi_value'] if s5 else '?'})
MACD: {sig_emoji(s5,'macd')} | BB: {sig_emoji(s5,'bb')}
Vol: {sig_emoji(s5,'volume')} | Mom: {sig_emoji(s5,'momentum')}

━━ {btc_text} ━━
{pool_text}

━━ 🎯 KẾT QUẢ ━━
Dự đoán: *{pred_emoji}*
Độ tin cậy: *{confidence}%*
Cược: *{bet_amount} BNB*
"""
            keyboard = [[InlineKeyboardButton("🛑 DỪNG BOT", callback_data="stop_bot")]]
            await context.bot.send_message(
                chat_id=chat_id,
                text=analysis_msg,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

            # Đặt cược
            bet_success = False
            tx_hash = ""

            if session["test_mode"]:
                bet_success = True
                tx_hash = "TEST_MODE"
            else:
                if wallet:
                    bet_success, tx_hash = place_bet(
                        w3, prediction_contract, user_id,
                        wallet["address"], epoch, prediction, bet_amount
                    )

            if not bet_success:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"❌ Đặt cược thất bại: {tx_hash}"
                )

            # Chờ round kết thúc
            close_time = round_info["close"]
            wait_time = close_time - int(time.time()) + 5
            if wait_time > 0:
                await asyncio.sleep(wait_time)

            # Kiểm tra kết quả
            result = None
            if session["test_mode"]:
                # Lấy kết quả thật từ chain
                for _ in range(10):
                    round_data = prediction_contract.functions.rounds(epoch).call()
                    if round_data[13]:  # oracle called
                        lock_p = round_data[4]
                        close_p = round_data[5]
                        actual = "UP" if close_p > lock_p else "DOWN"
                        won = actual == prediction
                        reward = bet_amount * (pool["bull_payout"] if prediction == "UP" else pool["bear_payout"]) if won else 0
                        result = {
                            "won": won,
                            "bet": prediction,
                            "result": actual,
                            "amount": bet_amount,
                            "reward": reward,
                            "claimed": True
                        }
                        break
                    await asyncio.sleep(5)
            else:
                if wallet:
                    for _ in range(10):
                        result = check_round_result(
                            prediction_contract, epoch, wallet["address"]
                        )
                        if result:
                            break
                        await asyncio.sleep(5)

                    # Claim nếu thắng
                    if result and result["won"] and not result["claimed"]:
                        claim_ok, claim_tx = claim_winnings(
                            w3, prediction_contract, user_id,
                            wallet["address"], [epoch]
                        )

            # Cập nhật session
            if result:
                gas = gas_cost if not session["test_mode"] else 0
                session["gas_used"] += gas
                session["total_rounds"] += 1

                if result["won"]:
                    net_profit = result["reward"] - bet_amount - gas
                    session["balance"] += net_profit
                    session["profit"] += net_profit
                    session["wins"] += 1
                    session["consecutive_losses"] = 0
                    session["current_streak"] = max(0, session["current_streak"]) + 1
                    session["max_consecutive_wins"] = max(
                        session["max_consecutive_wins"], session["current_streak"]
                    )
                    result_emoji = "✅ THẮNG"
                    result_profit = f"+{net_profit:.4f} BNB"
                else:
                    loss = bet_amount + gas
                    session["balance"] -= loss
                    session["profit"] -= loss
                    session["losses"] += 1
                    session["consecutive_losses"] += 1
                    session["current_streak"] = min(0, session["current_streak"]) - 1
                    session["max_consecutive_losses"] = max(
                        session["max_consecutive_losses"],
                        abs(session["current_streak"])
                    )
                    result_emoji = "❌ THUA"
                    result_profit = f"-{loss:.4f} BNB"

                win_rate = session["wins"] / session["total_rounds"] * 100
                profit_pct = (session["profit"] / session["initial_balance"] * 100) if session["initial_balance"] > 0 else 0

                result_msg = f"""
{'✅' if result['won'] else '❌'} *ROUND #{epoch} - {result_emoji}*
━━━━━━━━━━━━━━━━━━

🎯 Đặt: {result['bet']} | Kết quả: {result['result']}
💰 Cược: {bet_amount:.4f} BNB
{'💵 Thắng' if result['won'] else '💸 Thua'}: {result_profit}
⛽ Gas: {gas:.4f} BNB

━━ 📊 THỐNG KÊ ━━
💼 Số dư: {session['balance']:.4f} BNB
📈 Lãi/Lỗ: {session['profit']:+.4f} BNB ({profit_pct:+.1f}%)
🎯 Tỉ lệ thắng: {session['wins']}/{session['total_rounds']} ({win_rate:.1f}%)
{'🔥 Thắng liên tiếp: ' + str(session['current_streak']) if session['current_streak'] > 0 else '💀 Thua liên tiếp: ' + str(abs(session['current_streak']))}
"""
                keyboard = [[InlineKeyboardButton("🛑 DỪNG BOT", callback_data="stop_bot")]]
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=result_msg,
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )

            # Kiểm tra điều kiện dừng
            profit_pct = (session["profit"] / session["initial_balance"] * 100) if session["initial_balance"] > 0 else 0
            stop_reason = None

            if session["balance"] <= 0.005:
                stop_reason = "💸 Hết tiền!"
            elif profit_pct >= session["stop_profit_pct"]:
                stop_reason = f"🎯 Đạt mục tiêu lãi {session['stop_profit_pct']}%!"
            elif profit_pct <= -session["stop_loss_pct"]:
                stop_reason = f"🛑 Đạt ngưỡng lỗ {session['stop_loss_pct']}%!"
            elif session["max_rounds"] > 0 and session["total_rounds"] >= session["max_rounds"]:
                stop_reason = f"✅ Hoàn thành {session['max_rounds']} rounds!"

            if stop_reason:
                session["running"] = False
                win_rate = session["wins"] / session["total_rounds"] * 100 if session["total_rounds"] > 0 else 0
                final_msg = f"""
🏁 *BOT ĐÃ DỪNG*
━━━━━━━━━━━━━━━━━━

Lý do: {stop_reason}

━━ 📊 KẾT QUẢ PHIÊN ━━
⏱️ Tổng rounds: {session['total_rounds']}
✅ Thắng: {session['wins']} ({win_rate:.1f}%)
❌ Thua: {session['losses']}
⛽ Gas: {session['gas_used']:.4f} BNB

💰 Vốn đầu: {session['initial_balance']:.4f} BNB
💼 Số dư: {session['balance']:.4f} BNB
📈 Lãi/Lỗ: {session['profit']:+.4f} BNB ({profit_pct:+.1f}%)

🔥 Thắng liên tiếp max: {session['max_consecutive_wins']}
💀 Thua liên tiếp max: {session['max_consecutive_losses']}
"""
                keyboard = [
                    [InlineKeyboardButton("▶️ Chạy tiếp", callback_data="run_bot"),
                     InlineKeyboardButton("🔄 Phiên mới", callback_data="test_mode")],
                    [InlineKeyboardButton("🏠 Menu", callback_data="back_main")]
                ]
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=final_msg,
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return

        except Exception as e:
            logger.error(f"Bot loop error: {e}")
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ Lỗi: {str(e)}\nĐang thử lại sau 30 giây..."
            )
            await asyncio.sleep(30)

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    logger.info("BNB Prediction Bot đang chạy...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
