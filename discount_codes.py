"""
discount_codes.py
مدیریت کدهای تخفیف قابل استفاده در فرآیند خرید (/addvpn) برای کاربران عادی.

هر کد می‌تواند:
- درصدی باشد (مثلاً ۲۰٪ تخفیف) یا مبلغ ثابت (مثلاً ۱۰۰,۰۰۰ تومان تخفیف).
- محدودیت تعداد استفاده داشته باشد (یا نامحدود).
- تاریخ انقضا داشته باشد (یا بدون انقضا).
- فعال/غیرفعال شود بدون نیاز به حذف کامل.

ذخیره‌سازی ساده و پایدار روی یک فایل JSON کنار پروژه (مثل بقیه‌ی ماژول‌های store).
کدها همیشه با حروف بزرگ (upper-case) نگه‌داری و جستجو می‌شوند تا حساسیت به
بزرگ/کوچک بودن حروف مشکلی ایجاد نکند.

ساختار هر رکورد:
{
    "type": "percent" | "fixed",
    "value": <عدد>،  # درصد (۱ تا ۱۰۰) یا مبلغ ثابت به تومان
    "max_uses": <عدد> | null,   # null یعنی نامحدود
    "used_count": <عدد>,
    "active": true/false,
    "created_at": <unix timestamp>,
    "expires_at": <unix timestamp> | null   # null یعنی بدون انقضا
}
"""

import json
import os
import time
import threading
import random
import string
from datetime import datetime

_LOCK = threading.Lock()
_STORE_PATH = os.path.join(os.path.dirname(__file__), "discount_codes_store.json")


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


def _normalize(code: str) -> str:
    return (code or "").strip().upper()


def _generate_unique_code(prefix: str = "WELCOME", length: int = 6) -> str:
    """ساخت یک کد تصادفی که با هیچ کد موجودی برخورد نداشته باشد (برای کدهای خودکار خوش‌آمد)"""
    chars = string.ascii_uppercase + string.digits
    for _ in range(20):  # عملاً هرگز به این تعداد تلاش نیاز نمی‌شود
        candidate = prefix + "".join(random.choices(chars, k=length))
        if not get_code(candidate):
            return candidate
    # اگر خیلی بدشانس بودیم، طول را افزایش می‌دهیم تا برخورد غیرممکن شود
    return prefix + "".join(random.choices(chars, k=length + 4))


def create_welcome_code(discount_type: str, value: float, expires_at: float = None) -> str:
    """
    ساخت یک کد تخفیف یک‌بارمصرف اختصاصی برای یک کاربر تازه‌وارد (هدیه خوش‌آمدگویی).
    همیشه max_uses=1 است تا فقط اولین خرید همان کاربر از آن استفاده کند.
    کد ساخته‌شده را برمی‌گرداند.
    """
    code = _generate_unique_code()
    create_code(code, discount_type, value, max_uses=1, expires_at=expires_at, source="welcome")
    return code


def create_code(code: str, type_: str, value: float, max_uses: int = None, expires_at: float = None,
                 source: str = "admin") -> str:
    """ساخت کد تخفیف جدید. اگر کد از قبل وجود داشته باشد، مقدارش بازنویسی می‌شود.

    source مشخص می‌کند این کد را ادمین دستی ساخته ("admin") یا خود ربات
    به‌صورت خودکار برای خوش‌آمدگویی کاربر جدید ساخته ("welcome"). این فقط
    برای فیلتر کردن نمایش لیست در پنل ادمین استفاده می‌شود؛ روی اعتبارسنجی
    و مصرف کد هیچ تاثیری ندارد."""
    normalized = _normalize(code)
    with _LOCK:
        data = _load()
        data[normalized] = {
            "type": type_,
            "value": value,
            "max_uses": max_uses,
            "used_count": 0,
            "active": True,
            "created_at": time.time(),
            "expires_at": expires_at,
            "source": source,
        }
        _save(data)
        return normalized


def get_code(code: str):
    with _LOCK:
        data = _load()
        return data.get(_normalize(code))


def _infer_source(code: str, entry: dict) -> str:
    """برای رکوردهای قدیمی که قبل از افزودن فیلد source ساخته شده‌اند، بر اساس پیشوند حدس می‌زنیم"""
    if "source" in entry:
        return entry["source"]
    return "welcome" if code.upper().startswith("WELCOME") else "admin"


def list_codes(source: str = None) -> dict:
    """
    کدها را برمی‌گرداند، مرتب‌شده بر اساس جدیدترین.
    source=None یعنی همه؛ source="admin" یعنی فقط کدهایی که ادمین با /addcode
    دستی ساخته؛ source="welcome" یعنی فقط کدهای خودکار خوش‌آمدگویی کاربر جدید.
    """
    with _LOCK:
        data = _load()
        if source:
            data = {c: e for c, e in data.items() if _infer_source(c, e) == source}
        return dict(sorted(data.items(), key=lambda kv: kv[1].get("created_at", 0), reverse=True))


def delete_code(code: str) -> bool:
    normalized = _normalize(code)
    with _LOCK:
        data = _load()
        if normalized not in data:
            return False
        del data[normalized]
        _save(data)
        return True


def set_active(code: str, active: bool) -> bool:
    normalized = _normalize(code)
    with _LOCK:
        data = _load()
        if normalized not in data:
            return False
        data[normalized]["active"] = active
        _save(data)
        return True


def record_usage(code: str):
    """افزایش شمارنده استفاده؛ معمولاً بعد از تایید نهایی پرداخت توسط ادمین صدا زده می‌شود."""
    normalized = _normalize(code)
    with _LOCK:
        data = _load()
        if normalized in data:
            data[normalized]["used_count"] = data[normalized].get("used_count", 0) + 1
            _save(data)


def validate(code_raw: str):
    """
    بررسی معتبر بودن یک کد برای استفاده همین الان.
    خروجی: (ok: bool, reason: str, entry: dict|None)
    reason فقط وقتی ok=False پر می‌شود و مستقیم قابل نمایش به کاربر است.
    """
    normalized = _normalize(code_raw)
    if not normalized:
        return False, "کد تخفیف نمی‌تواند خالی باشد.", None

    entry = get_code(normalized)
    if not entry:
        return False, "کد تخفیف نامعتبر است.", None

    if not entry.get("active", True):
        return False, "این کد تخفیف غیرفعال شده است.", None

    expires_at = entry.get("expires_at")
    if expires_at and time.time() > expires_at:
        return False, "این کد تخفیف منقضی شده است.", None

    max_uses = entry.get("max_uses")
    if max_uses is not None and entry.get("used_count", 0) >= max_uses:
        return False, "ظرفیت استفاده از این کد تخفیف تمام شده است.", None

    return True, "", entry


def compute_discount_amount(entry: dict, base_amount: int) -> int:
    """مبلغ تخفیف (به تومان) برای یک مبلغ پایه مشخص؛ هرگز بیشتر از خود مبلغ پایه نمی‌شود."""
    if not entry or base_amount <= 0:
        return 0
    if entry.get("type") == "percent":
        amount = round(base_amount * float(entry.get("value", 0)) / 100)
    else:
        amount = int(entry.get("value", 0))
    return max(0, min(amount, base_amount))


def format_summary(code: str, entry: dict) -> str:
    """متن قابل نمایش برای ادمین (لیست /codes)."""
    type_label = "درصدی" if entry.get("type") == "percent" else "مبلغ ثابت"
    if entry.get("type") == "percent":
        value_label = f"{entry.get('value'):g}٪"
    else:
        value_label = f"{int(entry.get('value', 0)):,} تومان"

    max_uses = entry.get("max_uses")
    used = entry.get("used_count", 0)
    usage_label = f"{used} / {max_uses}" if max_uses is not None else f"{used} / نامحدود"

    expires_at = entry.get("expires_at")
    expiry_label = datetime.fromtimestamp(expires_at).strftime("%Y-%m-%d") if expires_at else "بدون انقضا"

    status = "✅ فعال" if entry.get("active", True) else "⏸ غیرفعال"

    return (
        f"🏷 کد: {code}\n"
        f"نوع: {type_label} ({value_label})\n"
        f"تعداد استفاده: {usage_label}\n"
        f"انقضا: {expiry_label}\n"
        f"وضعیت: {status}"
    )
