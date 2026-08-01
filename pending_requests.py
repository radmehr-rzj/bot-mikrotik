"""
pending_requests.py
مدیریت درخواست‌های در انتظار تایید ادمین برای پرداخت کارت‌به‌کارت.

فرآیند کلی:
1. مشتری تعرفه را انتخاب می‌کند، یوزرنیم/پسورد را وارد می‌کند و رسید پرداخت را ارسال می‌کند.
2. یک رکورد "در انتظار" (pending) ساخته می‌شود و برای ادمین با دکمه تایید/رد ارسال می‌شود.
3. ادمین با زدن دکمه، درخواست را تایید یا رد می‌کند.
4. در صورت تایید، اکانت روی میکروتیک ساخته و اطلاعات به مشتری تحویل داده می‌شود.

ذخیره‌سازی ساده و پایدار روی یک فایل JSON کنار پروژه (بین ری‌استارت‌های ربات هم باقی می‌ماند).
"""

import json
import os
import time
import threading
import uuid

_LOCK = threading.Lock()
_STORE_PATH = os.path.join(os.path.dirname(__file__), "pending_requests_store.json")


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


def create_request(username: str, password: str, profile: str, price: str,
                    telegram_user_id: int, telegram_display: str, chat_id: int,
                    extra_users: int = 0) -> str:
    """ساخت یک درخواست جدید در انتظار تایید؛ شناسه کوتاه آن را برمی‌گرداند."""
    with _LOCK:
        data = _load()
        request_id = uuid.uuid4().hex[:10]
        data[request_id] = {
            "username": username,
            "password": password,
            "profile": profile,
            "price": price,
            "telegram_user_id": telegram_user_id,
            "telegram_display": telegram_display,
            "chat_id": chat_id,
            "extra_users": extra_users,
            "status": "pending",
            "created_at": time.time(),
        }
        _save(data)
        return request_id


def get_request(request_id: str):
    with _LOCK:
        data = _load()
        return data.get(request_id)


def update_status(request_id: str, status: str):
    with _LOCK:
        data = _load()
        if request_id in data:
            data[request_id]["status"] = status
            _save(data)


def username_pending(username: str) -> bool:
    """آیا یوزرنیمی مشابه (بدون توجه به بزرگ/کوچک بودن حروف) در درخواست‌های در انتظار تایید هست؟"""
    normalized = (username or "").strip().lower()
    with _LOCK:
        data = _load()
        for req in data.values():
            if req.get("status") != "pending":
                continue
            if (req.get("username") or "").strip().lower() == normalized:
                return True
        return False


def has_pending_for_user(telegram_user_id: int, timeout_hours: float = None) -> bool:
    """
    آیا این کاربر همین الان یک درخواست در انتظار تایید دارد؟
    اگر timeout_hours داده شود، درخواست‌های قدیمی‌تر از این مدت خودکار
    منقضی (expired) می‌شوند و دیگر مانع کاربر نمی‌شوند.
    """
    with _LOCK:
        data = _load()
        changed = False
        result = False
        now = time.time()

        for req in data.values():
            if req.get("telegram_user_id") != telegram_user_id or req.get("status") != "pending":
                continue

            if timeout_hours is not None:
                age_hours = (now - req.get("created_at", now)) / 3600
                if age_hours >= timeout_hours:
                    req["status"] = "expired"
                    changed = True
                    continue

            result = True

        if changed:
            _save(data)

        return result


def list_pending(timeout_hours: float = None) -> list:
    """
    لیست تمام درخواست‌های در انتظار تایید (برای دستور /pending ادمین).
    درخواست‌های منقضی‌شده به‌صورت خودکار وضعیت‌شان به‌روزرسانی و از لیست خارج می‌شوند.
    خروجی: لیستی از (request_id, request_dict)
    """
    with _LOCK:
        data = _load()
        changed = False
        now = time.time()
        result = []

        for request_id, req in data.items():
            if req.get("status") != "pending":
                continue

            if timeout_hours is not None:
                age_hours = (now - req.get("created_at", now)) / 3600
                if age_hours >= timeout_hours:
                    req["status"] = "expired"
                    changed = True
                    continue

            result.append((request_id, req))

        if changed:
            _save(data)

        return result
