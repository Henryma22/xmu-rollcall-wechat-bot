from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from .config import CONFIG_DIR, ensure_config_dir

WECHAT_BOT_CONFIG_FILE = CONFIG_DIR / "wechat_bot_config.json"
DEFAULT_WECHAT_BOT_CONFIG = {
    "version": 1,
    "users": {},
}


def load_wechat_bot_config() -> Dict[str, Any]:
    ensure_config_dir()
    if WECHAT_BOT_CONFIG_FILE.exists():
        try:
            with open(WECHAT_BOT_CONFIG_FILE, "r", encoding="utf-8") as file_obj:
                payload = json.load(file_obj)
                if isinstance(payload, dict):
                    payload.setdefault("version", 1)
                    payload.setdefault("users", {})
                    return payload
        except Exception:
            pass
    return {
        "version": DEFAULT_WECHAT_BOT_CONFIG["version"],
        "users": {},
    }


def save_wechat_bot_config(config: Dict[str, Any]) -> None:
    ensure_config_dir()
    with open(WECHAT_BOT_CONFIG_FILE, "w", encoding="utf-8") as file_obj:
        json.dump(config, file_obj, indent=2, ensure_ascii=False)


def _ensure_user(config: Dict[str, Any], user_id: str) -> Dict[str, Any]:
    users = config.setdefault("users", {})
    user_config = users.setdefault(
        user_id,
        {
            "accounts": [],
            "current_account_id": None,
        },
    )
    user_config.setdefault("accounts", [])
    user_config.setdefault("current_account_id", None)
    return user_config


def _get_next_account_id(accounts: List[Dict[str, Any]]) -> int:
    if not accounts:
        return 1
    return max(int(account.get("id", 0)) for account in accounts) + 1


def get_user_accounts(user_id: str) -> List[Dict[str, Any]]:
    config = load_wechat_bot_config()
    user_config = _ensure_user(config, user_id)
    return list(user_config.get("accounts", []))


def get_user_account_by_id(user_id: str, account_id: int) -> Optional[Dict[str, Any]]:
    for account in get_user_accounts(user_id):
        if int(account.get("id", 0)) == int(account_id):
            return account
    return None


def get_current_user_account(user_id: str) -> Optional[Dict[str, Any]]:
    config = load_wechat_bot_config()
    user_config = _ensure_user(config, user_id)
    current_account_id = user_config.get("current_account_id")
    if current_account_id is None:
        return None
    for account in user_config.get("accounts", []):
        if int(account.get("id", 0)) == int(current_account_id):
            return account
    return None


def add_or_update_user_account(
    user_id: str,
    username: str,
    password: str,
    name: str,
) -> Tuple[Dict[str, Any], bool]:
    config = load_wechat_bot_config()
    user_config = _ensure_user(config, user_id)
    accounts = user_config.get("accounts", [])

    for account in accounts:
        if account.get("username") == username:
            account["password"] = password
            account["name"] = name
            user_config["current_account_id"] = account.get("id")
            save_wechat_bot_config(config)
            return account, False

    account = {
        "id": _get_next_account_id(accounts),
        "name": name,
        "username": username,
        "password": password,
    }
    accounts.append(account)
    user_config["current_account_id"] = account["id"]
    save_wechat_bot_config(config)
    return account, True


def set_current_user_account(user_id: str, account_id: int) -> Optional[Dict[str, Any]]:
    config = load_wechat_bot_config()
    user_config = _ensure_user(config, user_id)
    for account in user_config.get("accounts", []):
        if int(account.get("id", 0)) == int(account_id):
            user_config["current_account_id"] = int(account_id)
            save_wechat_bot_config(config)
            return account
    return None


def build_user_session_cache_key(user_id: str, account_id: int) -> str:
    user_hash = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:16]
    return f"wechat_{user_hash}_{account_id}"
