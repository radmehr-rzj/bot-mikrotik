"""
rate_limit.py
یک محدودکننده‌ی ساده و مبتنی بر فایل JSON، برای جلوگیری از ساخت بی‌رویه
یوزر VPN توسط کاربران عادی (غیر ادمین) تلگرام.

هر رکورد: {telegram_user_id: last_request_timestamp}
این فایل بین ری‌استارت‌های ربات هم باقی می‌ماند (پایدار روی دیسک).
"""

import json
import os
import time
import threading

_LOCK = threading.Lock()
_STORE_PATH = os.path.join(os.path.dirname(__file__), "rate_limit_store.json")


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


def seconds_until_allowed(user_id: int, cooldown_hours: float) -> float:
    """
    اگر کاربر مجاز به درخواست جدید باشد 0 برمی‌گرداند،
    در غیر این صورت تعداد ثانیه‌های باقی‌مانده تا مجاز شدن را برمی‌گرداند.
    """
    with _LOCK:
        data = _load()
        last = data.get(str(user_id))
        if last is None:
            return 0.0

        cooldown_seconds = cooldown_hours * 3600
        elapsed = time.time() - last
        remaining = cooldown_seconds - elapsed
        return max(0.0, remaining)


def register_request(user_id: int):
    """ثبت زمان درخواست موفق کاربر"""
    with _LOCK:
        data = _load()
        data[str(user_id)] = time.time()
        _save(data)
