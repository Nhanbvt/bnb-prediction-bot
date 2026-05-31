from mnemonic import Mnemonic
from eth_account import Account
from cryptography.fernet import Fernet
import hashlib, base64, os

Account.enable_unaudited_hdwallet_features()

def generate_key_from_password(password: str) -> bytes:
    key = hashlib.sha256(password.encode()).digest()
    return base64.urlsafe_b64encode(key)

def encrypt_data(data: str, user_id: int) -> str:
    key = generate_key_from_password(str(user_id) + "bnbbot_salt_2024")
    f = Fernet(key)
    return f.encrypt(data.encode()).decode()

def decrypt_data(encrypted: str, user_id: int) -> str:
    key = generate_key_from_password(str(user_id) + "bnbbot_salt_2024")
    f = Fernet(key)
    return f.decrypt(encrypted.encode()).decode()

def wallet_from_seed(seed_phrase: str):
    try:
        Account.enable_unaudited_hdwallet_features()
        account = Account.from_mnemonic(seed_phrase)
        return {
            "address": account.address,
            "private_key": account.key.hex()
        }
    except Exception as e:
        return None

def wallet_from_private_key(private_key: str):
    try:
        if not private_key.startswith("0x"):
            private_key = "0x" + private_key
        account = Account.from_key(private_key)
        return {
            "address": account.address,
            "private_key": private_key
        }
    except:
        return None

def validate_seed_phrase(phrase: str) -> bool:
    words = phrase.strip().split()
    return len(words) == 12

# Lưu ví user (encrypted)
user_wallets = {}

def save_wallet(user_id: int, wallet_data: dict):
    encrypted_key = encrypt_data(wallet_data["private_key"], user_id)
    user_wallets[user_id] = {
        "address": wallet_data["address"],
        "encrypted_key": encrypted_key
    }

def get_wallet(user_id: int):
    return user_wallets.get(user_id)

def get_private_key(user_id: int) -> str:
    wallet = user_wallets.get(user_id)
    if not wallet:
        return None
    return decrypt_data(wallet["encrypted_key"], user_id)
