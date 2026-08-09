"""
backup.py
ساخت و بازیابی یک فایل بک‌آپ واحد (JSON) شامل تمام داده‌های پایدار ربات:
- تنظیمات پنل مدیریت (settings_store.json)
- درخواست‌های در انتظار تایید (pending_requests_store.json)
- محدودیت زمانی کاربران (rate_limit_store.json)
- فایل کانفیگ مشترک OpenVPN (vpn_client.ovpn) که بعد از هر خرید برای مشتری ارسال می‌شود

نکته مهم: این بک‌آپ شامل خود اکانت‌های VPN (روی User Manager میکروتیک) نیست؛
آن‌ها روی خود روتر ذخیره می‌شوند و باید جداگانه از میکروتیک بک‌آپ گرفته شوند
(مثلاً با /system backup save یا /export در RouterOS). این بک‌آپ فقط
وضعیت داخلی خود ربات (تنظیمات، صف تایید پرداخت، محدودیت‌ها، فایل کانفیگ) را پوشش می‌دهد.

فرمت فایل بک‌آپ:
{
    "version": 3,
    "created_at": <unix timestamp>,
    "data": {
        "settings": {...},
        "pending_requests": {...},
        "rate_limit": {...},
        "customer_accounts": {...},
        "bot_users": {...},
        "discount_codes": {...}
    },
    "files": {
        "ovpn_config": {"filename": "vpn_client.ovpn", "content_b64": "..."}
    }
}
"""

import base64
import json
import os
import time

BASE_DIR = os.path.dirname(__file__)

# نگاشت نام بخش (JSON key-value) به اسم فایل واقعی روی دیسک
STORE_FILES = {
    "settings": "settings_store.json",
    "pending_requests": "pending_requests_store.json",
    "rate_limit": "rate_limit_store.json",
    "customer_accounts": "customer_accounts_store.json",
    "bot_users": "bot_users_store.json",
    "discount_codes": "discount_codes_store.json",
}

# فایل‌های باینری/متنی که عیناً (نه به‌صورت JSON) باید در بک‌آپ باشند
BINARY_FILES = {
    "ovpn_config": "vpn_client.ovpn",
}

BACKUP_VERSION = 3


def create_backup_bytes() -> bytes:
    """ساخت بک‌آپ کامل از همه فایل‌های ذخیره‌سازی، به‌صورت bytes آماده ارسال/ذخیره"""
    bundle = {
        "version": BACKUP_VERSION,
        "created_at": time.time(),
        "data": {},
        "files": {},
    }

    for section, filename in STORE_FILES.items():
        path = os.path.join(BASE_DIR, filename)
        content = {}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = json.load(f)
            except (json.JSONDecodeError, OSError):
                content = {}
        bundle["data"][section] = content

    for key, filename in BINARY_FILES.items():
        path = os.path.join(BASE_DIR, filename)
        if os.path.exists(path):
            with open(path, "rb") as f:
                raw = f.read()
            bundle["files"][key] = {
                "filename": filename,
                "content_b64": base64.b64encode(raw).decode("ascii"),
            }

    return json.dumps(bundle, ensure_ascii=False, indent=2).encode("utf-8")


def backup_filename() -> str:
    from datetime import datetime
    return f"mikrotik_vpn_bot_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"


def validate_backup(raw_bytes: bytes) -> dict:
    """
    فایل بک‌آپ را پارس و اعتبارسنجی می‌کند (بدون نوشتن روی دیسک).
    خطا در صورت نامعتبر بودن ساختار پرتاب می‌شود.
    خروجی: دیکشنری bundle پارس‌شده

    نکته: بخش "files" اختیاری است (بک‌آپ‌های قدیمی‌تر ممکن است نداشته باشند)
    تا بازیابی بک‌آپ‌های قبلی هم بدون خطا کار کند.
    """
    try:
        bundle = json.loads(raw_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ValueError(f"فایل بک‌آپ معتبر نیست (JSON غیرقابل خواندن): {e}")

    if not isinstance(bundle, dict) or "data" not in bundle:
        raise ValueError("ساختار فایل بک‌آپ نامعتبر است (کلید 'data' یافت نشد).")

    # توجه: عمداً حضور تک‌تک بخش‌های STORE_FILES بررسی نمی‌شود، چون بک‌آپ‌های
    # قدیمی‌تر (قبل از افزودن بخش‌های جدید مثل customer_accounts) ممکن است
    # بعضی از این بخش‌ها را نداشته باشند؛ در بازیابی، بخش‌های غایب خالی در نظر
    # گرفته می‌شوند (نه خطا).
    return bundle


def restore_from_bundle(bundle: dict) -> dict:
    """
    محتوای bundle معتبرشده را روی فایل‌های واقعی می‌نویسد.
    خروجی: تعداد رکوردهای هر بخش JSON (برای نمایش خلاصه به ادمین)؛
    فایل‌های باینری (مثل کانفیگ OpenVPN) هم اگر در بک‌آپ باشند بازنویسی می‌شوند.
    """
    counts = {}
    for section, filename in STORE_FILES.items():
        content = bundle["data"].get(section, {})
        path = os.path.join(BASE_DIR, filename)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(content, f, ensure_ascii=False, indent=2)
        counts[section] = len(content) if isinstance(content, dict) else 0

    for key, filename in BINARY_FILES.items():
        file_entry = bundle.get("files", {}).get(key)
        if not file_entry:
            continue
        path = os.path.join(BASE_DIR, filename)
        raw = base64.b64decode(file_entry["content_b64"])
        with open(path, "wb") as f:
            f.write(raw)
        counts[key] = 1

    return counts
