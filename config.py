"""
config.py
خواندن تنظیمات از .env (به‌عنوان مقدار پیش‌فرض) به‌همراه پشتیبانی از
override پویا از طریق پنل مدیریت ادمین داخل خود ربات (settings_store.py).

استفاده در کد دقیقاً مثل قبل است: Config.CARD_NUMBER, Config.MIKROTIK_HOST و ...
هر بار که این مقادیر خوانده شوند، اول settings_store چک می‌شود و اگر ادمین
مقداری برایش تنظیم کرده باشد همان استفاده می‌شود، وگرنه مقدار .env.

BOT_TOKEN و ADMIN_ID عمداً از این مکانیزم مستثنا هستند و فقط از .env خوانده
می‌شوند؛ چون تغییر این دو از داخل ربات می‌تواند باعث قفل شدن دسترسی ادمین شود.
"""

import os
from dotenv import load_dotenv

import settings_store

load_dotenv()

# مقادیر پیش‌فرض خوانده‌شده از .env
_ENV_DEFAULTS = {
    "MIKROTIK_HOST": os.getenv("MIKROTIK_HOST", ""),
    "MIKROTIK_USER": os.getenv("MIKROTIK_USER", ""),
    "MIKROTIK_PASSWORD": os.getenv("MIKROTIK_PASSWORD", ""),
    "MIKROTIK_PORT": os.getenv("MIKROTIK_PORT", "8728"),
    "MIKROTIK_USE_SSL": os.getenv("MIKROTIK_USE_SSL", "false"),

    "PROFILE_1_MONTH": os.getenv("PROFILE_1_MONTH", "30Day"),
    "PROFILE_2_MONTH": os.getenv("PROFILE_2_MONTH", "60Day"),

    "SELF_SERVICE_COOLDOWN_HOURS": os.getenv("SELF_SERVICE_COOLDOWN_HOURS", "24"),
    "PENDING_REQUEST_TIMEOUT_HOURS": os.getenv("PENDING_REQUEST_TIMEOUT_HOURS", "48"),

    "CARD_NUMBER": os.getenv("CARD_NUMBER", ""),
    "CARD_HOLDER_NAME": os.getenv("CARD_HOLDER_NAME", ""),
    "PRICE_1_MONTH": os.getenv("PRICE_1_MONTH", ""),
    "PRICE_2_MONTH": os.getenv("PRICE_2_MONTH", ""),

    "SUPPORT_USERNAME": os.getenv("SUPPORT_USERNAME", ""),

    # آدرسی که در آموزش‌های اتصال به مشتری نشان داده می‌شود (دامنه/IP واقعی سرویس VPN)
    # عمداً از MIKROTIK_HOST جداست، چون آن IP فقط برای دسترسی API ربات محدود شده
    # و نباید در اختیار مشتری‌ها قرار گیرد.
    "VPN_SERVER_HOST": os.getenv("VPN_SERVER_HOST", ""),

    # هزینه هر کاربر اضافه (device اضافه؛ یعنی shared-users بیشتر از ۱) به تفکیک تعرفه
    "EXTRA_USER_PRICE_1_MONTH": os.getenv("EXTRA_USER_PRICE_1_MONTH", "400,000 تومان"),
    "EXTRA_USER_PRICE_2_MONTH": os.getenv("EXTRA_USER_PRICE_2_MONTH", "800,000 تومان"),
    # حداکثر تعداد کاربر اضافه‌ای که می‌توان هنگام خرید انتخاب کرد
    "MAX_EXTRA_USERS": os.getenv("MAX_EXTRA_USERS", "5"),

    "BACKUP_AUTO_ENABLED": os.getenv("BACKUP_AUTO_ENABLED", "true"),
    "BACKUP_INTERVAL_HOURS": os.getenv("BACKUP_INTERVAL_HOURS", "24"),

    # کد تخفیف خودکاری که برای هر کاربر تازه‌وارد (بعد از اولین /start) ساخته و ارسال می‌شود
    "WELCOME_DISCOUNT_ENABLED": os.getenv("WELCOME_DISCOUNT_ENABLED", "true"),
    "WELCOME_DISCOUNT_TYPE": os.getenv("WELCOME_DISCOUNT_TYPE", "percent"),  # percent یا fixed
    "WELCOME_DISCOUNT_VALUE": os.getenv("WELCOME_DISCOUNT_VALUE", "10"),
    "WELCOME_DISCOUNT_EXPIRY_DAYS": os.getenv("WELCOME_DISCOUNT_EXPIRY_DAYS", "7"),  # 0 = بدون انقضا

    "TUTORIAL_L2TP": os.getenv("TUTORIAL_L2TP", (
        "📱 آموزش اتصال L2TP\n\n"
        "۱. به تنظیمات VPN دستگاه خود بروید (Settings > VPN > Add VPN).\n"
        "۲. نوع اتصال را روی L2TP/IPSec PSK قرار دهید.\n"
        "۳. آدرس سرور: {host}\n"
        "۴. یوزرنیم و پسوردی که از ربات دریافت کرده‌اید را وارد کنید.\n"
        "۵. Pre-shared key (در صورت نیاز) را از پشتیبانی بپرسید.\n"
        "۶. اتصال را ذخیره و روشن کنید."
    )),
    "TUTORIAL_OVPN": os.getenv("TUTORIAL_OVPN", (
        "🔐 آموزش اتصال OpenVPN\n\n"
        "۱. اپلیکیشن OpenVPN Connect را از استور نصب کنید.\n"
        "۲. فایل کانفیگ (.ovpn) پیوست‌شده را وارد اپلیکیشن کنید (Import Profile).\n"
        "۳. یوزرنیم و پسوردی که از ربات دریافت کرده‌اید را وارد کنید.\n"
        "۴. آدرس سرور: {host}\n"
        "۵. دکمه Connect را بزنید."
    )),
}

# نوع هر فیلد، برای تبدیل درست مقدار وقتی خوانده می‌شود
_FLOAT_KEYS = {"SELF_SERVICE_COOLDOWN_HOURS", "PENDING_REQUEST_TIMEOUT_HOURS", "BACKUP_INTERVAL_HOURS", "WELCOME_DISCOUNT_VALUE"}
_INT_KEYS = {"MIKROTIK_PORT", "MAX_EXTRA_USERS", "WELCOME_DISCOUNT_EXPIRY_DAYS"}
_BOOL_KEYS = {"MIKROTIK_USE_SSL", "BACKUP_AUTO_ENABLED", "WELCOME_DISCOUNT_ENABLED"}


class _ConfigMeta(type):
    def __getattr__(cls, name):
        if name not in _ENV_DEFAULTS:
            raise AttributeError(name)

        raw = settings_store.get(name)
        if raw is None:
            raw = _ENV_DEFAULTS[name]

        if name in _FLOAT_KEYS:
            try:
                return float(raw)
            except (TypeError, ValueError):
                return 0.0
        if name in _INT_KEYS:
            try:
                return int(raw)
            except (TypeError, ValueError):
                return 0
        if name in _BOOL_KEYS:
            return str(raw).lower() in ("true", "1", "yes", "بله")
        return raw


class Config(metaclass=_ConfigMeta):
    # --- Telegram: عمداً غیرقابل تغییر از داخل ربات ---
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    ADMIN_ID: int = int(os.getenv("ADMIN_ID", "0"))

    # بقیه‌ی فیلدها (MIKROTIK_HOST, CARD_NUMBER, ...) به‌صورت پویا
    # از طریق _ConfigMeta.__getattr__ در بالا مدیریت می‌شوند.
