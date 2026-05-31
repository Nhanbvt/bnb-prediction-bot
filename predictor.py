from web3 import Web3
from config import *
from analyzer import (
    get_bnb_klines, get_btc_trend, get_chainlink_price,
    get_binance_price, analyze_signals, get_round_pool_info
)
from wallet import get_private_key
import time

# Kết nối BSC
def get_w3():
    w3 = Web3(Web3.HTTPProvider(BSC_RPC))
    if not w3.is_connected():
        w3 = Web3(Web3.HTTPProvider(BSC_RPC_BACKUP))
    return w3

def get_contracts(w3):
    prediction = w3.eth.contract(
        address=Web3.to_checksum_address(PREDICTION_CONTRACT),
        abi=PREDICTION_ABI
    )
    chainlink = w3.eth.contract(
        address=Web3.to_checksum_address(CHAINLINK_CONTRACT),
        abi=CHAINLINK_ABI
    )
    return prediction, chainlink

def get_balance(w3, address):
    balance = w3.eth.get_balance(address)
    return round(w3.from_wei(balance, "ether"), 6)

def get_current_epoch(prediction_contract):
    return prediction_contract.functions.currentEpoch().call()

def get_round_times(prediction_contract, epoch):
    round_data = prediction_contract.functions.rounds(epoch).call()
    return {
        "start": round_data[1],
        "lock": round_data[2],
        "close": round_data[3],
        "lock_price": round_data[4] / 1e8 if round_data[4] else 0,
        "close_price": round_data[5] / 1e8 if round_data[5] else 0,
    }

def check_round_result(prediction_contract, epoch, address):
    try:
        round_data = prediction_contract.functions.rounds(epoch).call()
        ledger = prediction_contract.functions.ledger(epoch, address).call()

        if not round_data[13]:  # oracleCalled
            return None

        lock_price = round_data[4]
        close_price = round_data[5]
        position = ledger[0]  # 0=Bull, 1=Bear
        amount = ledger[1] / 1e18
        claimed = ledger[2]

        if amount == 0:
            return None

        result = "UP" if close_price > lock_price else "DOWN"
        bet = "UP" if position == 0 else "DOWN"
        won = result == bet

        reward = 0
        if won and not claimed:
            total = round_data[8] / 1e18
            side = round_data[9] / 1e18 if position == 0 else round_data[10] / 1e18
            if side > 0:
                reward = amount * (total / side)

        return {
            "won": won,
            "bet": bet,
            "result": result,
            "amount": round(amount, 6),
            "reward": round(reward, 6),
            "claimed": claimed
        }
    except:
        return None

def place_bet(w3, prediction_contract, user_id, address, epoch, prediction, amount_bnb):
    try:
        private_key = get_private_key(user_id)
        if not private_key:
            return False, "Không tìm thấy private key"

        amount_wei = w3.to_wei(amount_bnb, "ether")
        nonce = w3.eth.get_transaction_count(address)
        gas_price = w3.to_wei(GAS_PRICE_GWEI, "gwei")

        if prediction == "UP":
            tx = prediction_contract.functions.betBull(epoch).build_transaction({
                "from": address,
                "value": amount_wei,
                "gas": GAS_LIMIT,
                "gasPrice": gas_price,
                "nonce": nonce,
            })
        else:
            tx = prediction_contract.functions.betBear(epoch).build_transaction({
                "from": address,
                "value": amount_wei,
                "gas": GAS_LIMIT,
                "gasPrice": gas_price,
                "nonce": nonce,
            })

        signed = w3.eth.account.sign_transaction(tx, private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)

        if receipt.status == 1:
            return True, tx_hash.hex()
        else:
            return False, "Transaction thất bại"
    except Exception as e:
        return False, str(e)

def claim_winnings(w3, prediction_contract, user_id, address, epochs):
    try:
        private_key = get_private_key(user_id)
        nonce = w3.eth.get_transaction_count(address)
        gas_price = w3.to_wei(GAS_PRICE_GWEI, "gwei")

        tx = prediction_contract.functions.claim(epochs).build_transaction({
            "from": address,
            "gas": GAS_LIMIT,
            "gasPrice": gas_price,
            "nonce": nonce,
        })

        signed = w3.eth.account.sign_transaction(tx, private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)

        return receipt.status == 1, tx_hash.hex()
    except Exception as e:
        return False, str(e)

def full_analysis(w3, prediction_contract, chainlink_contract):
    df_1m = get_bnb_klines("1m", 100)
    df_5m = get_bnb_klines("5m", 50)
    btc = get_btc_trend()
    cl_price = get_chainlink_price(w3, chainlink_contract)
    bn_price = get_binance_price()
    signals_1m = analyze_signals(df_1m)
    signals_5m = analyze_signals(df_5m)

    epoch = get_current_epoch(prediction_contract)
    pool = get_round_pool_info(prediction_contract, epoch)

    price_diff = 0
    if cl_price and bn_price:
        price_diff = abs(cl_price - bn_price) / bn_price * 100

    # Kết hợp 1m và 5m
    prediction = "UP"
    confidence = 50
    if signals_1m and signals_5m:
        up = signals_1m["up_count"] + signals_5m["up_count"]
        down = signals_1m["down_count"] + signals_5m["down_count"]
        total = up + down
        prediction = "UP" if up >= down else "DOWN"
        confidence = round(max(up, down) / total * 100, 1)
    elif signals_1m:
        prediction = signals_1m["prediction"]
        confidence = signals_1m["confidence"]

    # BTC bonus
    if btc:
        if btc["trend"] == prediction:
            confidence = min(confidence + 5, 99)

    return {
        "prediction": prediction,
        "confidence": confidence,
        "signals_1m": signals_1m,
        "signals_5m": signals_5m,
        "btc": btc,
        "chainlink_price": cl_price,
        "binance_price": bn_price,
        "price_diff": round(price_diff, 3),
        "pool": pool,
        "epoch": epoch,
    }
