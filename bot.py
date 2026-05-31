async def run_bot_loop(context, user_id: int, chat_id: int):
    session = get_session(user_id)
    w3 = get_w3()
    prediction_contract, chainlink_contract = get_contracts(w3)
    wallet = get_wallet(user_id)
    
    last_bet_epoch = None
    pending_claim_epoch = None

    while session["running"]:
        try:
            now = int(time.time())
            epoch = get_current_epoch(prediction_contract)
            round_info = get_round_times(prediction_contract, epoch)
            lock_time = round_info["lock"]
            close_time = round_info["close"]
            time_to_lock = lock_time - now

            # === BƯỚC 1: CLAIM ROUND TRƯỚC NẾU CÓ ===
            if pending_claim_epoch and pending_claim_epoch != epoch:
                result = None
                for _ in range(12):
                    try:
                        round_data = prediction_contract.functions.rounds(pending_claim_epoch).call()
                        if round_data[13]:  # oracle called
                            lock_p = round_data[4]
                            close_p = round_data[5]
                            actual = "UP" if close_p > lock_p else "DOWN"

                            if not session["test_mode"] and wallet:
                                ledger = prediction_contract.functions.ledger(
                                    pending_claim_epoch, wallet["address"]
                                ).call()
                                position = ledger[0]
                                amount = ledger[1] / 1e18
                                claimed = ledger[2]
                                bet_dir = "UP" if position == 0 else "DOWN"
                                won = actual == bet_dir

                                pool = get_round_pool_info(prediction_contract, pending_claim_epoch)
                                reward = 0
                                if won and amount > 0 and pool:
                                    payout = pool["bull_payout"] if bet_dir == "UP" else pool["bear_payout"]
                                    reward = amount * payout

                                result = {
                                    "won": won,
                                    "bet": bet_dir,
                                    "result": actual,
                                    "amount": amount,
                                    "reward": reward,
                                    "claimed": claimed
                                }

                                # Claim nếu thắng
                                if won and not claimed:
                                    claim_ok, claim_tx = claim_winnings(
                                        w3, prediction_contract, user_id,
                                        wallet["address"], [pending_claim_epoch]
                                    )
                            else:
                                # Test mode
                                pool = get_round_pool_info(prediction_contract, pending_claim_epoch)
                                won = actual == session.get("last_prediction", "UP")
                                amount = session.get("last_bet_amount", 0)
                                reward = amount * (pool["bull_payout"] if session.get("last_prediction") == "UP" else pool["bear_payout"]) if won and pool else 0
                                result = {
                                    "won": won,
                                    "bet": session.get("last_prediction", "UP"),
                                    "result": actual,
                                    "amount": amount,
                                    "reward": reward,
                                    "claimed": True
                                }
                            break
                    except:
                        pass
                    await asyncio.sleep(5)

                # Cập nhật session và gửi kết quả
                if result:
                    gas = 0.0005 if not session["test_mode"] else 0
                    session["gas_used"] += gas
                    session["total_rounds"] += 1

                    if result["won"]:
                        net_profit = result["reward"] - result["amount"] - gas
                        session["balance"] += net_profit
                        session["profit"] += net_profit
                        session["wins"] += 1
                        session["consecutive_losses"] = 0
                        session["current_streak"] = max(0, session.get("current_streak", 0)) + 1
                        session["max_consecutive_wins"] = max(
                            session["max_consecutive_wins"], session["current_streak"]
                        )
                        result_text = f"✅ THẮNG +{net_profit:.4f} BNB"
                    else:
                        loss = result["amount"] + gas
                        session["balance"] -= loss
                        session["profit"] -= loss
                        session["losses"] += 1
                        session["consecutive_losses"] = session.get("consecutive_losses", 0) + 1
                        session["current_streak"] = min(0, session.get("current_streak", 0)) - 1
                        session["max_consecutive_losses"] = max(
                            session["max_consecutive_losses"],
                            abs(session["current_streak"])
                        )
                        result_text = f"❌ THUA -{result['amount'] + gas:.4f} BNB"

                    win_rate = session["wins"] / session["total_rounds"] * 100 if session["total_rounds"] > 0 else 0
                    profit_pct = (session["profit"] / session["initial_balance"] * 100) if session["initial_balance"] > 0 else 0
                    streak = session.get("current_streak", 0)
                    streak_text = f"🔥 Thắng liên tiếp: {streak}" if streak > 0 else f"💀 Thua liên tiếp: {abs(streak)}"

                    result_msg = f"""
{'✅' if result['won'] else '❌'} *ROUND #{pending_claim_epoch} - {result_text}*
━━━━━━━━━━━━━━━━━━

🎯 Đặt: {result['bet']} | Kết quả: {result['result']}
💰 Cược: {result['amount']:.4f} BNB
⛽ Gas: {gas:.4f} BNB

━━ 📊 THỐNG KÊ ━━
💼 Số dư: {session['balance']:.4f} BNB
📈 Lãi/Lỗ: {session['profit']:+.4f} BNB ({profit_pct:+.1f}%)
🎯 Tỉ lệ: {session['wins']}/{session['total_rounds']} ({win_rate:.1f}%)
{streak_text}
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
                        stop_reason = "💸 Số dư quá thấp!"
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

⏱️ Tổng rounds: {session['total_rounds']}
✅ Thắng: {session['wins']} ({win_rate:.1f}%)
❌ Thua: {session['losses']}
⛽ Gas: {session['gas_used']:.4f} BNB

💰 Vốn đầu: {session['initial_balance']:.4f} BNB
💼 Số dư: {session['balance']:.4f} BNB
📈 Lãi/Lỗ: {session['profit']:+.4f} BNB ({profit_pct:+.1f}%)
🔥 Thắng max: {session['max_consecutive_wins']}
💀 Thua max: {session['max_consecutive_losses']}
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

                pending_claim_epoch = None

            # === BƯỚC 2: PHÂN TÍCH & ĐẶT CƯỢC ROUND TIẾP THEO ===
            next_epoch = epoch + 1

            if last_bet_epoch != next_epoch:
                # Chờ đến 30 giây trước lock
                now = int(time.time())
                time_to_lock = lock_time - now
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

                # Tính cược 5% số dư hiện tại
                bet_amount = round(session["balance"] * 0.05, 6)
                gas_cost = 0.0005
                if not session["test_mode"]:
                    bet_amount = max(bet_amount - gas_cost, 0.001)

                def sig_emoji(s, key):
                    if not s:
                        return "❓"
                    return "⬆️" if s["signals"][key] == "UP" else "⬇️"

                btc_text = f"₿ BTC: {btc['change_5m']:+.2f}% {'⬆️' if btc['trend'] == 'UP' else '⬇️'}" if btc else ""
                pool_text = (f"🐂 Bull: {pool['bull_pct']}% (x{pool['bull_payout']})\n"
                             f"🐻 Bear: {pool['bear_pct']}% (x{pool['bear_payout']})") if pool else ""
                pred_emoji = "⬆️ UP" if prediction == "UP" else "⬇️ DOWN"
                mode_text = "🧪 TEST" if session["test_mode"] else "💰 LIVE"

                analysis_msg = f"""
🔮 *PHÂN TÍCH ROUND #{next_epoch}* {mode_text}
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
Cược Round #{next_epoch}: *{bet_amount:.4f} BNB*
"""
                keyboard = [[InlineKeyboardButton("🛑 DỪNG BOT", callback_data="stop_bot")]]
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=analysis_msg,
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )

                # Đặt cược
                if session["test_mode"]:
                    session["last_prediction"] = prediction
                    session["last_bet_amount"] = bet_amount
                    last_bet_epoch = next_epoch
                    pending_claim_epoch = next_epoch
                else:
                    if wallet:
                        bet_ok, tx_hash = place_bet(
                            w3, prediction_contract, user_id,
                            wallet["address"], next_epoch, prediction, bet_amount
                        )
                        if bet_ok:
                            session["last_prediction"] = prediction
                            session["last_bet_amount"] = bet_amount
                            last_bet_epoch = next_epoch
                            pending_claim_epoch = next_epoch
                            await context.bot.send_message(
                                chat_id=chat_id,
                                text=f"✅ Đã đặt {bet_amount:.4f} BNB → {pred_emoji}\n"
                                     f"TX: `{tx_hash[:20]}...`",
                                parse_mode="Markdown"
                            )
                        else:
                            await context.bot.send_message(
                                chat_id=chat_id,
                                text=f"❌ Đặt cược thất bại: {tx_hash}"
                            )

            # === BƯỚC 3: CHỜ ROUND HIỆN TẠI KẾT THÚC ===
            now = int(time.time())
            wait_time = close_time - now + 8
            if wait_time > 0:
                await asyncio.sleep(wait_time)

        except Exception as e:
            logger.error(f"Bot loop error: {e}")
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ Lỗi: {str(e)}\nThử lại sau 15 giây..."
            )
            await asyncio.sleep(15)
