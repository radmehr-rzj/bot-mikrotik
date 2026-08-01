"""
bot_users.py
ثبت هر کاربر تلگرامی که حداقل یک‌بار /start زده، برای:
- اطلاع‌رسانی فوری به ادمین هنگام ورود کاربر جدید
- نمایش آمار کلی کاربران ربات (/stats)

ذخیره‌سازی ساده و پایدار روی یک فایل JSON کنار پروژه.
ساختار:
{
    "<telegram_user_id>": {"display_name": "...", "first_seen": <ts>}
}
"""

import json
import os
import time
import threading

_LOCK = threading.Lock()
_STORE_PATH = os.path.join(os.path.dirname(__file__), "bot_users_store.json")


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


def register_and_check_new(user_id: int, display_name: str) -> bool:
    """
    اگر این کاربر برای اولین‌بار است، رکوردش را ثبت می‌کند و True برمی‌گرداند.
    اگر قبلاً ثبت شده بود، False برمی‌گرداند (فقط برای جلوگیری از اطلاع‌رسانی تکراری).
    """
    with _LOCK:
        data = _load()
        key = str(user_id)
        if key in data:
            return False
        data[key] = {"display_name": display_name, "first_seen": time.time()}
        _save(data)
        return True


def get_total_count() -> int:
    with _LOCK:
        return len(_load())


def get_recent(limit: int = 10) -> list:
    """لیست آخرین کاربران بر اساس زمان اولین ورود، جدیدترین اول"""
    with _LOCK:
        data = _load()
        items = [(int(uid), entry) for uid, entry in data.items()]
        items.sort(key=lambda x: x[1].get("first_seen", 0), reverse=True)
        return items[:limit]
