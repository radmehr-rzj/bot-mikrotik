"""
customer_accounts.py
نگه‌داری نگاشت «کدام کاربر تلگرام چه یوزرنیم‌های VPN‌ای خریده»، برای بخش
«📦 اکانت‌های من» که مشتری بتواند اکانت‌های خودش را ببیند.

ذخیره‌سازی ساده و پایدار روی یک فایل JSON کنار پروژه.
ساختار:
{
    "<telegram_user_id>": [
        {"username": "...", "profile": "...", "shared_users": 2, "created_at": <ts>},
        ...
    ]
}
"""

import json
import os
import time
import threading

_LOCK = threading.Lock()
_STORE_PATH = os.path.join(os.path.dirname(__file__), "customer_accounts_store.json")


def _load() -> dict:
    if not os.path.exists(_STORE_PATH):
        return {}
    try:
        with open(_STORE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict):
    with open(_STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def record_purchase(telegram_user_id: int, username: str, profile: str, shared_users: int = 1):
    """ثبت یک خرید موفق برای کاربر تلگرام مشخص‌شده"""
    with _LOCK:
        data = _load()
        key = str(telegram_user_id)
        entries = data.get(key, [])
        entries.append({
            "username": username,
            "profile": profile,
            "shared_users": shared_users,
            "created_at": time.time(),
        })
        data[key] = entries
        _save(data)


def get_purchases(telegram_user_id: int) -> list:
    """لیست تمام خریدهای ثبت‌شده برای یک کاربر تلگرام"""
    with _LOCK:
        data = _load()
        return data.get(str(telegram_user_id), [])
