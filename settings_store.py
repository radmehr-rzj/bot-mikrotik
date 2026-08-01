"""
settings_store.py
ذخیره‌سازی مقادیر override شده که ادمین از داخل خود ربات (پنل مدیریت) تغییر می‌دهد.
اگر مقداری اینجا ست نشده باشد، مقدار پیش‌فرض از فایل .env خوانده می‌شود
(نگاه کنید به config.py).

این فایل بین ری‌استارت‌های ربات هم باقی می‌ماند.
"""

import json
import os
import threading

_LOCK = threading.Lock()
_STORE_PATH = os.path.join(os.path.dirname(__file__), "settings_store.json")


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


def get(key: str, default=None):
    with _LOCK:
        data = _load()
        return data.get(key, default)


def set(key: str, value):
    with _LOCK:
        data = _load()
        data[key] = value
        _save(data)


def get_all() -> dict:
    with _LOCK:
        return _load()
