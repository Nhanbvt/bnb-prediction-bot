import os

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")

# BSC RPC
BSC_RPC = "https://bsc-dataseed1.binance.org/"
BSC_RPC_BACKUP = "https://bsc-dataseed2.binance.org/"

# PancakeSwap Prediction Contract
PREDICTION_CONTRACT = "0x18B2A687610328590Bc8F2e5fEdDe3b582A49cdA"

# Chainlink BNB/USD Oracle
CHAINLINK_CONTRACT = "0x0567F2323251f0Aab15c8dFb1967E4e8A7D42aeE"

# Binance API
BINANCE_API = "https://api.binance.com/api/v3"

# Gas settings
GAS_LIMIT = 300000
GAS_PRICE_GWEI = 3

# Bot settings
BET_PERCENT = 5
MIN_BALANCE = 0.01
CONSECUTIVE_LOSS_LIMIT = 5
PAUSE_AFTER_LOSS = 1800  # 30 phút

# PancakeSwap ABI (rút gọn)
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
