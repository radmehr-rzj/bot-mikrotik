"""
bot.py
ربات تلگرام مدیریت یوزرهای OpenVPN / L2TP در User Manager میکروتیک (RouterOS 7.23)
شامل فرآیند پرداخت کارت‌به‌کارت با تایید ادمین برای کاربران عادی.

اجرا:
    python bot.py

پیش‌نیاز: فایل .env کنار همین فایل با مقادیر لازم (نمونه: .env.example)
"""

import logging
import asyncio
import re
import time
import os
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from config import Config
from mikrotik_api import MikrotikManager, DuplicateUserError, UserNotFoundError, MikrotikError
import rate_limit
import pending_requests
import settings_store
import backup as backup_module
import customer_accounts
import bot_users

# ---------------------- تنظیمات لاگ ----------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------------------- استیت‌های ConversationHandler ----------------------
ADD_USERNAME, ADD_PASSWORD, ADD_PROFILE, ADD_EXTRA_USERS, ADD_RECEIPT = range(5)
DEL_USERNAME = 100

# فقط حروف انگلیسی، عدد، نقطه، خط تیره و آندرلاین؛ طول ۳ تا ۳۲ کاراکتر
_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{3,32}$")

# ---------------------- دکمه‌های منوی اصلی (Inline - زیر خود پیام در چت) ----------------------
MENU_BUY_CB = "menu_buy"
MENU_LIST_CB = "menu_list"
MENU_DELETE_CB = "menu_delete"
MENU_SUPPORT_CB = "menu_support"
MENU_MANAGE_CB = "menu_manage"
MENU_HOME_CB = "menu_home"
MENU_TUTORIAL_CB = "menu_tutorial"
MENU_MY_ACCOUNTS_CB = "menu_my_accounts"
MENU_STATS_CB = "menu_stats"
TUTORIAL_L2TP_CB = "tut_l2tp"
TUTORIAL_OVPN_CB = "tut_ovpn"


def main_menu_keyboard(admin: bool) -> InlineKeyboardMarkup:
    """
    ساخت منوی دکمه‌ای که مستقیم زیر پیام در چت نمایش داده می‌شود (Inline)،
    نه به‌صورت منوی جدا در پایین صفحه.
    """
    if admin:
        rows = [
            [InlineKeyboardButton("🛒 خرید VPN جدید", callback_data=MENU_BUY_CB)],
            [
                InlineKeyboardButton("📋 لیست یوزرها", callback_data=MENU_LIST_CB),
                InlineKeyboardButton("🗑 حذف یوزر", callback_data=MENU_DELETE_CB),
            ],
            [
                InlineKeyboardButton("🧩 آموزش اتصال", callback_data=MENU_TUTORIAL_CB),
                InlineKeyboardButton("📦 اکانت‌های من", callback_data=MENU_MY_ACCOUNTS_CB),
            ],
            [InlineKeyboardButton("🐋 پشتیبانی", callback_data=MENU_SUPPORT_CB)],
            [
                InlineKeyboardButton("📊 آمار کاربران", callback_data=MENU_STATS_CB),
                InlineKeyboardButton("⚙️ مدیریت ربات", callback_data=MENU_MANAGE_CB),
            ],
        ]
    else:
        rows = [
            [InlineKeyboardButton("🛒 خرید VPN جدید", callback_data=MENU_BUY_CB)],
            [
                InlineKeyboardButton("🧩 آموزش اتصال", callback_data=MENU_TUTORIAL_CB),
                InlineKeyboardButton("📦 اکانت‌های من", callback_data=MENU_MY_ACCOUNTS_CB),
            ],
            [InlineKeyboardButton("🐋 پشتیبانی", callback_data=MENU_SUPPORT_CB)],
        ]
    return InlineKeyboardMarkup(rows)


def home_button_keyboard() -> InlineKeyboardMarkup:
    """یک دکمه کوچک «بازگشت به منو» که زیر پیام‌های پایانی (موفقیت/لغو/خطا) نمایش داده می‌شود"""
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 منوی اصلی", callback_data=MENU_HOME_CB)]])


async def menu_home_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش مجدد منوی اصلی با زدن دکمه «🏠 منوی اصلی»"""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    text = (
        "🤖 ربات مدیریت VPN میکروتیک\n\nیک گزینه را انتخاب کنید:"
        if is_admin(user_id)
        else "🤖 یک گزینه را انتخاب کنید:"
    )
    await context.bot.send_message(
        chat_id=query.message.chat.id,
        text=text,
        reply_markup=main_menu_keyboard(is_admin(user_id)),
    )


# دکمه شیشه‌ای لغو که در تمام مراحل مکالمه‌ای (addvpn / delvpn) کنار پیام نمایش داده می‌شود
CANCEL_INLINE_KEYBOARD = InlineKeyboardMarkup([[InlineKeyboardButton("🚫 لغو", callback_data="flow_cancel")]])


async def flow_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لغو هر مرحله از مکالمه (addvpn/delvpn) با زدن دکمه شیشه‌ای لغو"""
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    user_id = query.from_user.id
    await query.edit_message_text("❎ عملیات لغو شد.", reply_markup=home_button_keyboard())
    return ConversationHandler.END

# ---------------------- ساخت یک نمونه از مدیریت میکروتیک ----------------------
# نکته: دیگر پارامترهای اتصال در سازنده پاس داده نمی‌شود؛ MikrotikManager هر بار
# مستقیم از Config می‌خواند تا تغییرات پنل مدیریت بدون ری‌استارت اعمال شود.
mikrotik = MikrotikManager()


async def _reply(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, **kwargs):
    """
    ارسال پیام، چه در پاسخ به دستور متنی (/addvpn) و چه در پاسخ به دکمه شیشه‌ای منو،
    به‌صورت یکسان. این‌طوری همه توابع entry-point هم با دستور و هم با دکمه کار می‌کنند.
    """
    if update.callback_query:
        await update.callback_query.answer()
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text, **kwargs)
    else:
        await update.message.reply_text(text, **kwargs)


# ---------------------- دکوریتور محدودسازی به ادمین ----------------------
def admin_only(func):
    """فقط به Chat ID تعریف‌شده در .env پاسخ می‌دهد؛ بقیه نادیده گرفته می‌شوند."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id if update.effective_chat else None
        if chat_id != Config.ADMIN_ID:
            logger.warning(f"Unauthorized access attempt from chat_id={chat_id}")
            if update.callback_query:
                await update.callback_query.answer("⛔️ شما اجازه استفاده از این ربات را ندارید.", show_alert=True)
            elif update.message:
                await update.message.reply_text("⛔️ شما اجازه استفاده از این ربات را ندارید.")
            return ConversationHandler.END
        return await func(update, context)
    return wrapper


def is_admin(user_id: int) -> bool:
    return user_id == Config.ADMIN_ID


def _parse_price_to_number(price_str) -> int:
    """استخراج مقدار عددی از یک رشته قیمت آزاد (مثل '500,000 تومان' -> 500000)"""
    digits = re.sub(r"\D", "", str(price_str or ""))
    return int(digits) if digits else 0


def _format_toman(amount: int) -> str:
    return f"{amount:,} تومان"


def _create_vpn_account_text(username, password, profile, shared_users: int = 1) -> str:
    if shared_users > 1:
        device_line = f"📱 محدودیت: تا {shared_users} دستگاه همزمان قابل اتصال است"
    else:
        device_line = "📱 محدودیت: تک‌دستگاه (این اکانت فقط روی یک دستگاه همزمان قابل اتصال است)"
    return (
        "✅ اکانت VPN شما با موفقیت ساخته و فعال شد.\n\n"
        f"👤 یوزرنیم: {username}\n"
        f"🔑 پسورد: {password}\n"
        f"📅 پروفایل: {profile}\n"
        f"{device_line}"
    )


# ---------------------- /start ----------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else None
    requester = update.effective_user

    if is_admin(user_id):
        text = (
            "🤖 ربات مدیریت VPN میکروتیک (OpenVPN / L2TP - User Manager)\n\n"
            "از دکمه‌های زیر استفاده کنید، یا دستورات را مستقیم بزنید:\n"
            "/addvpn - ساخت یوزر جدید (بدون پرداخت)\n"
            "/delvpn - حذف یک یوزر\n"
            "/listvpn - نمایش لیست یوزرهای موجود\n"
            "/pending - لیست درخواست‌های در انتظار تایید\n"
            "/settings - پنل مدیریت کامل تنظیمات ربات\n"
            "/backup - دریافت فوری فایل بک‌آپ\n"
            "/restore - بازیابی از فایل بک‌آپ\n"
            "/tutorial - آموزش اتصال L2TP/OpenVPN\n"
            "/myaccounts - اکانت‌های VPN که خریداری کرده‌اید\n"
            "/setovpn - جایگزینی فایل کانفیگ OpenVPN\n"
            "/stats - آمار کاربران ربات\n"
            "/cancel - لغو عملیات جاری"
        )
    else:
        text = (
            "🤖 سلام! با این ربات می‌تونید یوزر VPN (OpenVPN / L2TP) بخرید.\n\n"
            "از دکمه زیر استفاده کنید، یا /addvpn را بزنید.\n"
            "/tutorial - آموزش اتصال L2TP/OpenVPN\n"
            "/myaccounts - اکانت‌های VPN که خریداری کرده‌اید\n"
            "/cancel - لغو عملیات جاری"
        )

    # ثبت کاربر جدید و اطلاع‌رسانی فوری به ادمین (فقط اگر خود ادمین نباشد)
    if requester is not None:
        display_name = f"@{requester.username}" if requester.username else requester.full_name
        is_new_user = bot_users.register_and_check_new(user_id, display_name)
        if is_new_user and not is_admin(user_id):
            try:
                total = bot_users.get_total_count()
                await context.bot.send_message(
                    chat_id=Config.ADMIN_ID,
                    text=(
                        "🆕 یک کاربر جدید ربات را استارت زد!\n\n"
                        f"💬 نام: {display_name}\n"
                        f"🆔 آیدی تلگرام: {user_id}\n"
                        f"👥 تعداد کل کاربران ربات: {total}"
                    ),
                )
            except Exception:
                logger.exception("Failed to notify admin about new user")

    # حذف صریح هر کیبورد ثابت (Reply Keyboard) قدیمی که ممکن است از نسخه‌های قبلی
    # ربات هنوز روی صفحه کاربر باقی مانده باشد. پیام موقت را بلافاصله بعد پاک می‌کنیم
    # تا هیچ اثری در چت باقی نماند.
    cleanup_msg = await update.message.reply_text("⌨️", reply_markup=ReplyKeyboardRemove())
    try:
        await cleanup_msg.delete()
    except Exception:
        pass  # اگر حذف ممکن نبود (مثلاً محدودیت زمانی تلگرام)، بی‌ضرر است

    await update.message.reply_text(text, reply_markup=main_menu_keyboard(is_admin(user_id)))


# ---------------------- دکمه پشتیبانی (منوی دائمی) ----------------------
def _format_tutorial(template: str, username: str, password: str) -> str:
    """جایگزینی {host}/{username}/{password} در متن آموزش؛ اگر ادمین متن را طوری ویرایش کرده
    باشد که پلیس‌هولدر اشتباهی داشته باشد، متن خام (بدون جایگزینی) برگردانده می‌شود."""
    try:
        return template.format(host=Config.VPN_SERVER_HOST, username=username, password=password)
    except (KeyError, IndexError, ValueError):
        return template


# مسیر فایل کانفیگ مشترک OpenVPN که برای همه مشتریان ارسال می‌شود
# (این فایل حاوی سرتیفیکیت/کلید مشترک است؛ احراز هویت واقعی هر مشتری از طریق
# یوزرنیم/پسورد اختصاصی‌اش که در User Manager ساخته شده انجام می‌شود، پس اشتراک
# همین یک فایل بین همه مشتریان مشکلی ایجاد نمی‌کند)
OVPN_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "vpn_client.ovpn")


async def _send_ovpn_config_file(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """اگر فایل کانفیگ OpenVPN روی دیسک موجود باشد، آن را برای کاربر ارسال می‌کند"""
    if not os.path.exists(OVPN_CONFIG_PATH):
        return
    try:
        with open(OVPN_CONFIG_PATH, "rb") as f:
            await context.bot.send_document(
                chat_id=chat_id,
                document=f,
                filename="vpn_client.ovpn",
                caption="📎 فایل کانفیگ OpenVPN — آن را داخل اپلیکیشن OpenVPN Connect ایمپورت کنید.",
            )
    except Exception:
        logger.exception("Failed to send OpenVPN config file")


async def send_purchase_tutorial(context: ContextTypes.DEFAULT_TYPE, chat_id: int, username: str, password: str):
    """ارسال خودکار آموزش اتصال L2TP و OpenVPN (+ فایل کانفیگ) بلافاصله بعد از تحویل اکانت به مشتری"""
    l2tp_text = _format_tutorial(Config.TUTORIAL_L2TP, username, password)
    ovpn_text = _format_tutorial(Config.TUTORIAL_OVPN, username, password)
    try:
        await context.bot.send_message(chat_id=chat_id, text=l2tp_text)
        await context.bot.send_message(chat_id=chat_id, text=ovpn_text)
        await _send_ovpn_config_file(context, chat_id)
        await context.bot.send_message(
            chat_id=chat_id, text="✅ آموزش و فایل کانفیگ ارسال شد.", reply_markup=home_button_keyboard()
        )
    except Exception:
        logger.exception("Failed to send connection tutorial")


async def menu_tutorial_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش انتخاب پروتکل (L2TP یا OpenVPN)؛ هم با دکمه منو و هم با دستور /tutorial کار می‌کند"""
    if update.callback_query:
        await update.callback_query.answer()
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📶 L2TP", callback_data=TUTORIAL_L2TP_CB),
            InlineKeyboardButton("🔐 OpenVPN", callback_data=TUTORIAL_OVPN_CB),
        ],
        [InlineKeyboardButton("🏠 منوی اصلی", callback_data=MENU_HOME_CB)],
    ])
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="کدام پروتکل مد نظرتان است؟",
        reply_markup=keyboard,
    )


async def tutorial_protocol_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش متن آموزش برای پروتکل انتخاب‌شده (با پلیس‌هولدر عمومی چون به اکانت خاصی وصل نیست)"""
    query = update.callback_query
    await query.answer()
    template = Config.TUTORIAL_L2TP if query.data == TUTORIAL_L2TP_CB else Config.TUTORIAL_OVPN
    text = _format_tutorial(template, "<یوزرنیم شما>", "<پسورد شما>")
    await context.bot.send_message(chat_id=query.message.chat.id, text=text)
    if query.data == TUTORIAL_OVPN_CB:
        await _send_ovpn_config_file(context, query.message.chat.id)
    await context.bot.send_message(
        chat_id=query.message.chat.id, text="⬆️ راهنما", reply_markup=home_button_keyboard()
    )


async def my_accounts_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش لیست اکانت‌های خریداری‌شده توسط همین کاربر تلگرام، همراه وضعیت زنده از میکروتیک"""
    if update.callback_query:
        await update.callback_query.answer()

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    purchases = customer_accounts.get_purchases(user_id)
    if not purchases:
        await context.bot.send_message(
            chat_id=chat_id,
            text="📭 شما هنوز هیچ اکانت VPN‌ای از این ربات نخریده‌اید.",
            reply_markup=home_button_keyboard(),
        )
        return

    status_msg = await context.bot.send_message(chat_id=chat_id, text="⏳ در حال دریافت وضعیت اکانت‌ها...")

    # نگاشت نام یوزر -> وضعیت زنده از میکروتیک (برای نمایش فعال/منقضی)
    live_by_name = {}
    try:
        live_users = await asyncio.to_thread(mikrotik.list_vpn_users)
        live_by_name = {u['name'].strip().lower(): u for u in live_users}
    except MikrotikError:
        logger.warning("Could not fetch live status for my_accounts_callback; showing without live state.")

    STATE_ICONS = {
        "running-active": "🟢 فعال",
        "used": "⚪ استفاده‌شده (قبلی)",
        "expired": "🔴 منقضی",
        "not-active": "🟡 غیرفعال",
    }

    lines = ["📦 اکانت‌های VPN شما:\n"]
    for p in purchases:
        live = live_by_name.get(p['username'].strip().lower())
        if live:
            status = STATE_ICONS.get(live.get('state'), f"❔ {live.get('state', 'نامشخص')}")
        else:
            status = "❔ روی روتر یافت نشد (شاید حذف شده)"
        shared = p.get('shared_users', 1)
        device_info = f" | {shared} دستگاه" if shared > 1 else ""
        lines.append(f"👤 {p['username']} | پروفایل: {p['profile']}{device_info} | {status}")

    await status_msg.edit_text("\n".join(lines), reply_markup=home_button_keyboard())


async def support_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if Config.SUPPORT_USERNAME:
        text = f"🐋 برای پشتیبانی با {Config.SUPPORT_USERNAME} در تلگرام در تماس باشید."
    else:
        text = "🐋 در حال حاضر آیدی پشتیبانی تنظیم نشده است."
    await _reply(update, context, text, reply_markup=home_button_keyboard())


# ==================================================================
#                        /addvpn Conversation
#   برای همه کاربران باز است. برای غیر ادمین‌ها، بعد از انتخاب تعرفه،
#   شماره کارت نشان داده می‌شود و باید رسید ارسال کنند؛ اکانت فقط بعد از
#   تایید ادمین ساخته می‌شود. خود ادمین این مسیر پرداخت را ندارد.
# ==================================================================
async def addvpn_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not is_admin(user_id):
        # ۱) بررسی محدودیت زمانی کلی
        remaining = rate_limit.seconds_until_allowed(user_id, Config.SELF_SERVICE_COOLDOWN_HOURS)
        if remaining > 0:
            hours = int(remaining // 3600)
            minutes = int((remaining % 3600) // 60)
            await _reply(
                update, context,
                f"⏳ شما اخیراً یک یوزر VPN ساخته‌اید.\n"
                f"لطفاً {hours} ساعت و {minutes} دقیقه دیگر دوباره تلاش کنید.",
                reply_markup=home_button_keyboard(),
            )
            return ConversationHandler.END

        # ۲) بررسی این‌که درخواست در انتظار تاییدی از قبل نداشته باشد
        # (درخواست‌های قدیمی‌تر از PENDING_REQUEST_TIMEOUT_HOURS خودکار منقضی می‌شوند)
        if pending_requests.has_pending_for_user(user_id, Config.PENDING_REQUEST_TIMEOUT_HOURS):
            await _reply(
                update, context,
                "⏳ شما یک درخواست در انتظار تایید ادمین دارید.\n"
                "لطفاً منتظر بررسی رسید قبلی بمانید.",
                reply_markup=home_button_keyboard(),
            )
            return ConversationHandler.END

    context.user_data.clear()
    await _reply(
        update, context,
        "👤 لطفاً نام کاربری (Username) جدید را وارد کنید:",
        reply_markup=CANCEL_INLINE_KEYBOARD,
    )
    return ADD_USERNAME


async def addvpn_get_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.text.strip()
    if not _USERNAME_PATTERN.match(username):
        await update.message.reply_text(
            "❗️ نام کاربری نامعتبر است.\n"
            "فقط حروف انگلیسی، عدد، نقطه، خط تیره و آندرلاین مجازند (۳ تا ۳۲ کاراکتر).\n"
            "دوباره وارد کنید:",
            reply_markup=CANCEL_INLINE_KEYBOARD,
        )
        return ADD_USERNAME

    # بررسی یوزرنیم مشابه (بدون توجه به بزرگ/کوچک بودن حروف)، هم در میکروتیک و هم در
    # درخواست‌های پرداختی که هنوز در انتظار تایید ادمین هستند.
    if pending_requests.username_pending(username):
        await update.message.reply_text(
            "❗️ یک درخواست دیگر با یوزرنیم مشابه در انتظار تایید است. لطفاً یوزرنیم دیگری وارد کنید:",
            reply_markup=CANCEL_INLINE_KEYBOARD,
        )
        return ADD_USERNAME

    await update.message.reply_text("⏳ در حال بررسی یوزرنیم روی میکروتیک...")
    try:
        existing_users = await asyncio.to_thread(mikrotik.list_vpn_users)
        normalized = username.lower()
        if any(u['name'].strip().lower() == normalized for u in existing_users):
            await update.message.reply_text(
                "❗️ این یوزرنیم قبلاً استفاده شده. لطفاً یوزرنیم دیگری وارد کنید:",
                reply_markup=CANCEL_INLINE_KEYBOARD,
            )
            return ADD_USERNAME
    except MikrotikError:
        # اگر ارتباط با میکروتیک برقرار نشد، اجازه ادامه می‌دهیم؛ چک نهایی موقع ساخت واقعی انجام می‌شود
        logger.warning("Could not pre-check username against Mikrotik; will rely on final check at creation time.")

    context.user_data['new_username'] = username
    await update.message.reply_text(
        "🔑 حالا رمز عبور (Password) را وارد کنید (حداقل ۴ کاراکتر):",
        reply_markup=CANCEL_INLINE_KEYBOARD,
    )
    return ADD_PASSWORD


async def addvpn_get_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = update.message.text.strip()
    if len(password) < 4:
        await update.message.reply_text(
            "❗️ رمز عبور باید حداقل ۴ کاراکتر باشد. دوباره وارد کنید:",
            reply_markup=CANCEL_INLINE_KEYBOARD,
        )
        return ADD_PASSWORD

    context.user_data['new_password'] = password

    price_1 = f" - {Config.PRICE_1_MONTH}" if Config.PRICE_1_MONTH else ""
    price_2 = f" - {Config.PRICE_2_MONTH}" if Config.PRICE_2_MONTH else ""

    # نکته: تلگرام رنگ سفارشی روی دکمه‌های inline پشتیبانی نمی‌کند؛
    # برای تمایز بصری از ایموجی رنگی استفاده می‌کنیم.
    keyboard = [
        [InlineKeyboardButton(f"🟢 ۱ ماهه{price_1}", callback_data="profile_30d")],
        [InlineKeyboardButton(f"🟢 ۲ ماهه{price_2}", callback_data="profile_60d")],
        [InlineKeyboardButton("🚫 لغو", callback_data="flow_cancel")],
    ]
    await update.message.reply_text(
        "📅 مدت اعتبار اکانت را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return ADD_PROFILE


async def addvpn_get_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    profile_map = {
        "profile_30d": (Config.PROFILE_1_MONTH, Config.PRICE_1_MONTH, Config.EXTRA_USER_PRICE_1_MONTH),
        "profile_60d": (Config.PROFILE_2_MONTH, Config.PRICE_2_MONTH, Config.EXTRA_USER_PRICE_2_MONTH),
    }
    profile, price, extra_price = profile_map.get(query.data, (None, "", ""))

    context.user_data['profile'] = profile
    context.user_data['price'] = price
    context.user_data['extra_price'] = extra_price

    await query.edit_message_text(
        "👥 چند «کاربر اضافه» می‌خواهید؟\n\n"
        "هر کاربر اضافه یعنی یک دستگاه همزمان بیشتر که با همین یوزرنیم/پسورد "
        "می‌تواند وصل شود.\n"
        f"هزینه هر کاربر اضافه برای این تعرفه: {extra_price or 'رایگان'}\n\n"
        f"عدد مورد نظر را بین ۰ تا {Config.MAX_EXTRA_USERS} وارد کنید (0 اگر نمی‌خواهید):",
        reply_markup=CANCEL_INLINE_KEYBOARD,
    )
    return ADD_EXTRA_USERS


async def addvpn_get_extra_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    max_extra = Config.MAX_EXTRA_USERS
    if not raw.isdigit() or int(raw) < 0 or int(raw) > max_extra:
        await update.message.reply_text(
            f"❗️ لطفاً یک عدد معتبر بین ۰ تا {max_extra} وارد کنید:",
            reply_markup=CANCEL_INLINE_KEYBOARD,
        )
        return ADD_EXTRA_USERS

    extra_users = int(raw)
    context.user_data['extra_users'] = extra_users

    requester = update.effective_user
    username = context.user_data.get('new_username')
    password = context.user_data.get('new_password')
    profile = context.user_data.get('profile')
    shared_users = 1 + extra_users

    # ---------------- مسیر ادمین: بدون پرداخت، ساخت فوری ----------------
    if is_admin(requester.id):
        status_msg = await update.message.reply_text("⏳ در حال ساخت یوزر روی میکروتیک، لطفاً صبر کنید...")
        try:
            await asyncio.to_thread(mikrotik.add_vpn_user, username, password, profile, shared_users)
            await status_msg.edit_text(
                _create_vpn_account_text(username, password, profile, shared_users),
                reply_markup=home_button_keyboard(),
            )
            customer_accounts.record_purchase(requester.id, username, profile, shared_users)
            await send_purchase_tutorial(context, update.effective_chat.id, username, password)
        except DuplicateUserError as e:
            await status_msg.edit_text(f"⚠️ {e}", reply_markup=home_button_keyboard())
        except MikrotikError as e:
            await status_msg.edit_text(f"❌ خطا: {e}", reply_markup=home_button_keyboard())
        except Exception as e:
            logger.exception("Unexpected error in addvpn_get_extra_users (admin)")
            await status_msg.edit_text(f"❌ خطای غیرمنتظره: {e}", reply_markup=home_button_keyboard())
        finally:
            context.user_data.clear()
        return ConversationHandler.END

    # ---------------- مسیر کاربر عادی: نمایش شماره کارت و درخواست رسید ----------------
    if not Config.CARD_NUMBER:
        await update.message.reply_text(
            "❌ در حال حاضر امکان پرداخت فعال نیست. لطفاً بعداً یا از طریق پشتیبانی اقدام کنید."
        )
        context.user_data.clear()
        return ConversationHandler.END

    price = context.user_data.get('price')
    extra_price = context.user_data.get('extra_price')
    base_amount = _parse_price_to_number(price)
    extra_amount_each = _parse_price_to_number(extra_price)
    extra_amount_total = extra_amount_each * extra_users
    grand_total = base_amount + extra_amount_total

    extra_line = ""
    if extra_users > 0:
        extra_line = (
            f"👥 کاربر اضافه: {extra_users} × {_format_toman(extra_amount_each)} = "
            f"{_format_toman(extra_amount_total)}\n"
        )

    total_line = f"💰 مبلغ کل قابل پرداخت: {_format_toman(grand_total)}\n" if grand_total else ""
    holder_line = f"👤 به نام: {Config.CARD_HOLDER_NAME}\n" if Config.CARD_HOLDER_NAME else ""

    await update.message.reply_text(
        f"📅 تعرفه انتخابی: {profile}\n"
        f"💵 قیمت پایه: {price}\n"
        f"{extra_line}"
        f"{total_line}"
        "💳 لطفاً مبلغ را به شماره کارت زیر واریز کنید:\n\n"
        f"`{Config.CARD_NUMBER}`\n"
        f"{holder_line}\n"
        "📸 بعد از واریز، لطفاً عکس رسید پرداخت را همینجا ارسال کنید.",
        parse_mode="Markdown",
        reply_markup=CANCEL_INLINE_KEYBOARD,
    )
    return ADD_RECEIPT


async def addvpn_get_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text(
            "❗️ لطفاً رسید پرداخت را به‌صورت عکس ارسال کنید.",
            reply_markup=CANCEL_INLINE_KEYBOARD,
        )
        return ADD_RECEIPT

    requester = update.effective_user
    username = context.user_data.get('new_username')
    password = context.user_data.get('new_password')
    profile = context.user_data.get('profile')
    price = context.user_data.get('price')
    extra_users = context.user_data.get('extra_users', 0)
    extra_price = context.user_data.get('extra_price')

    base_amount = _parse_price_to_number(price)
    extra_amount_total = _parse_price_to_number(extra_price) * extra_users
    grand_total = base_amount + extra_amount_total

    photo_file_id = update.message.photo[-1].file_id  # بزرگترین سایز عکس

    request_id = pending_requests.create_request(
        username=username,
        password=password,
        profile=profile,
        price=_format_toman(grand_total) if grand_total else price,
        telegram_user_id=requester.id,
        telegram_display=(f"@{requester.username}" if requester.username else requester.full_name),
        chat_id=update.effective_chat.id,
        extra_users=extra_users,
    )

    display_name = f"@{requester.username}" if requester.username else requester.full_name
    admin_keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ تایید و ساخت اکانت", callback_data=f"approve_{request_id}"),
            InlineKeyboardButton("❌ رد رسید", callback_data=f"reject_{request_id}"),
        ]
    ])

    extra_caption_line = f"👥 کاربر اضافه: {extra_users}\n" if extra_users else ""
    caption = (
        "🧾 رسید پرداخت جدید\n\n"
        f"👤 یوزرنیم درخواستی: {username}\n"
        f"📅 پروفایل: {profile}\n"
        f"{extra_caption_line}"
        f"💰 مبلغ کل: {_format_toman(grand_total) if grand_total else 'نامشخص'}\n"
        f"💬 مشتری: {display_name} (ID: {requester.id})"
    )

    try:
        await context.bot.send_photo(
            chat_id=Config.ADMIN_ID,
            photo=photo_file_id,
            caption=caption,
            reply_markup=admin_keyboard,
        )
    except Exception:
        logger.exception("Failed to forward receipt to admin")
        await update.message.reply_text("❌ خطا در ارسال رسید به ادمین. لطفاً دوباره تلاش کنید یا با پشتیبانی تماس بگیرید.")
        context.user_data.clear()
        return ConversationHandler.END

    await update.message.reply_text(
        "✅ رسید شما دریافت و برای بررسی به ادمین ارسال شد.\n"
        "بعد از تایید، اطلاعات اکانت VPN همینجا برایتان ارسال می‌شود.",
        reply_markup=home_button_keyboard(),
    )

    context.user_data.clear()
    return ConversationHandler.END


# ==================================================================
#              پنل مدیریت کامل ادمین (/settings یا دکمه منو)
#   همه متغیرهای قابل تنظیم ربات از اینجا قابل مشاهده و تغییرند،
#   بدون نیاز به ورود به سرور و ویرایش دستی .env
# ==================================================================
SETTINGS_FIELD_SELECT, SETTINGS_WAIT_VALUE = range(200, 202)

# تعریف فیلدهای قابل ویرایش: کلید -> (برچسب نمایشی، نوع، حساس بودن مقدار)
SETTINGS_FIELDS = {
    "CARD_NUMBER": ("💳 شماره کارت", "str", False),
    "CARD_HOLDER_NAME": ("👤 نام صاحب کارت", "str", False),
    "PRICE_1_MONTH": ("💰 قیمت تعرفه ۱ ماهه", "str", False),
    "PRICE_2_MONTH": ("💰 قیمت تعرفه ۲ ماهه", "str", False),
    "PROFILE_1_MONTH": ("📅 نام پروفایل ۱ ماهه (میکروتیک)", "str", False),
    "PROFILE_2_MONTH": ("📅 نام پروفایل ۲ ماهه (میکروتیک)", "str", False),
    "SELF_SERVICE_COOLDOWN_HOURS": ("⏱ کول‌داون کاربر عادی (ساعت)", "float", False),
    "PENDING_REQUEST_TIMEOUT_HOURS": ("⏱ انقضای درخواست در انتظار (ساعت)", "float", False),
    "SUPPORT_USERNAME": ("🐋 آیدی پشتیبانی", "str", False),
    "VPN_SERVER_HOST": ("🌍 آدرس سرور برای مشتری (در آموزش)", "str", False),
    "EXTRA_USER_PRICE_1_MONTH": ("➕ قیمت کاربر اضافه - ۱ ماهه", "str", False),
    "EXTRA_USER_PRICE_2_MONTH": ("➕ قیمت کاربر اضافه - ۲ ماهه", "str", False),
    "MAX_EXTRA_USERS": ("👥 حداکثر تعداد کاربر اضافه مجاز", "int", False),
    "MIKROTIK_HOST": ("🌐 آی‌پی/هاست میکروتیک", "str", False),
    "MIKROTIK_USER": ("👤 یوزر API میکروتیک", "str", False),
    "MIKROTIK_PASSWORD": ("🔑 پسورد API میکروتیک", "str", True),
    "MIKROTIK_PORT": ("🔌 پورت API میکروتیک", "int", False),
    "MIKROTIK_USE_SSL": ("🔒 SSL میکروتیک", "bool", False),
    "BACKUP_AUTO_ENABLED": ("💾 بک‌آپ خودکار روزانه", "bool", False),
    "BACKUP_INTERVAL_HOURS": ("💾 فاصله بک‌آپ خودکار (ساعت)", "float", False),
    "TUTORIAL_L2TP": ("📱 متن آموزش اتصال L2TP", "str", False),
    "TUTORIAL_OVPN": ("🔐 متن آموزش اتصال OpenVPN", "str", False),
}


def _mask_value(key: str, value: str) -> str:
    """مخفی کردن بخشی از مقادیر حساس (مثل پسورد) هنگام نمایش در چت"""
    _, _, sensitive = SETTINGS_FIELDS[key]
    if not sensitive or not value:
        return value or "(خالی)"
    if len(value) <= 2:
        return "••••"
    return value[:2] + "•" * max(4, len(value) - 2)


def settings_panel_keyboard() -> InlineKeyboardMarkup:
    rows = []
    row = []
    for key, (label, _, _) in SETTINGS_FIELDS.items():
        row.append(InlineKeyboardButton(label, callback_data=f"setkey_{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("❌ بستن پنل", callback_data="settings_close")])
    return InlineKeyboardMarkup(rows)


@admin_only
async def settings_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _reply(
        update, context,
        "⚙️ پنل مدیریت کامل ربات\n\n"
        "هر متغیری را که می‌خواهید تغییر دهید انتخاب کنید:",
        reply_markup=settings_panel_keyboard(),
    )
    return SETTINGS_FIELD_SELECT


async def settings_field_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    key = query.data.split("_", 1)[1]
    label, field_type, sensitive = SETTINGS_FIELDS[key]
    current_value = getattr(Config, key)

    # فیلد بولی (SSL، بک‌آپ خودکار و ...) با دکمه روشن/خاموش، بدون نیاز به تایپ
    if field_type == "bool":
        context.user_data['settings_key'] = key
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ روشن (yes)", callback_data="boolval_yes"),
                InlineKeyboardButton("❌ خاموش (no)", callback_data="boolval_no"),
            ],
            [InlineKeyboardButton("🚫 لغو", callback_data="settings_cancel")],
        ])
        await query.edit_message_text(
            f"{label}\nوضعیت فعلی: {'روشن' if current_value else 'خاموش'}\n\nمقدار جدید را انتخاب کنید:",
            reply_markup=keyboard,
        )
        return SETTINGS_WAIT_VALUE

    context.user_data['settings_key'] = key
    display_value = _mask_value(key, str(current_value))
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🚫 لغو", callback_data="settings_cancel")]])
    await query.edit_message_text(
        f"{label}\nمقدار فعلی: {display_value}\n\nمقدار جدید را ارسال کنید:",
        reply_markup=keyboard,
    )
    return SETTINGS_WAIT_VALUE


async def settings_save_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = context.user_data.get('settings_key')
    if not key:
        await update.message.reply_text("❌ خطای داخلی؛ دوباره /settings را بزنید.")
        return ConversationHandler.END

    label, field_type, _ = SETTINGS_FIELDS[key]
    raw_value = update.message.text.strip()

    if field_type == "float":
        try:
            float(raw_value)
        except ValueError:
            await update.message.reply_text(
                "❗️ این مقدار باید عدد باشد (مثلاً 24). دوباره ارسال کنید:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🚫 لغو", callback_data="settings_cancel")]]),
            )
            return SETTINGS_WAIT_VALUE
    elif field_type == "int":
        try:
            int(raw_value)
        except ValueError:
            await update.message.reply_text(
                "❗️ این مقدار باید عدد صحیح باشد (مثلاً 8728). دوباره ارسال کنید:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🚫 لغو", callback_data="settings_cancel")]]),
            )
            return SETTINGS_WAIT_VALUE

    settings_store.set(key, raw_value)
    context.user_data.clear()

    await update.message.reply_text(f"✅ «{label}» با موفقیت به‌روزرسانی شد.")
    await update.message.reply_text(
        "⚙️ پنل مدیریت کامل ربات\n\nمتغیر دیگری برای تغییر انتخاب کنید یا پنل را ببندید:",
        reply_markup=settings_panel_keyboard(),
    )
    return SETTINGS_FIELD_SELECT


async def settings_toggle_bool(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    key = context.user_data.get('settings_key')
    if not key or SETTINGS_FIELDS.get(key, (None, None, None))[1] != "bool":
        await query.edit_message_text("❌ خطای داخلی؛ دوباره /settings را بزنید.")
        return ConversationHandler.END

    label = SETTINGS_FIELDS[key][0]
    value = "true" if query.data == "boolval_yes" else "false"
    settings_store.set(key, value)
    context.user_data.clear()

    await query.edit_message_text(f"✅ «{label}» روی «{'روشن' if value == 'true' else 'خاموش'}» تنظیم شد.")
    await context.bot.send_message(
        chat_id=query.message.chat.id,
        text="⚙️ متغیر دیگری برای تغییر انتخاب کنید یا پنل را ببندید:",
        reply_markup=settings_panel_keyboard(),
    )
    return SETTINGS_FIELD_SELECT


async def settings_cancel_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لغو ویرایش همین فیلد و بازگشت به منوی اصلی پنل (نه خروج کامل)"""
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.edit_message_text(
        "⚙️ پنل مدیریت کامل ربات\n\nمتغیری برای تغییر انتخاب کنید یا پنل را ببندید:",
        reply_markup=settings_panel_keyboard(),
    )
    return SETTINGS_FIELD_SELECT


async def settings_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.edit_message_text("✅ پنل مدیریت بسته شد.", reply_markup=home_button_keyboard())
    return ConversationHandler.END


# ==================================================================
#                  بک‌آپ‌گیری و بازیابی (فقط ادمین)
# ==================================================================
RESTORE_WAIT_FILE, RESTORE_CONFIRM = range(300, 302)


@admin_only
async def backup_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ساخت و ارسال فوری یک فایل بک‌آپ کامل"""
    await update.message.reply_text("⏳ در حال ساخت فایل بک‌آپ...")
    raw = backup_module.create_backup_bytes()
    filename = backup_module.backup_filename()

    await context.bot.send_document(
        chat_id=update.effective_chat.id,
        document=raw,
        filename=filename,
        caption=(
            "💾 بک‌آپ کامل داده‌های ربات\n\n"
            "شامل: تنظیمات پنل مدیریت، درخواست‌های در انتظار تایید، محدودیت‌های زمانی کاربران.\n"
            "⚠️ شامل اکانت‌های VPN روی خود میکروتیک نمی‌شود؛ آن‌ها را جداگانه از خود روتر بک‌آپ بگیرید "
            "(مثلاً با /system backup save در RouterOS).\n"
            "این فایل را جایی امن نگه دارید (می‌توانید همینجا در تلگرام بایگانی‌اش کنید)."
        ),
    )


@admin_only
async def restore_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📥 فایل بک‌آپ (json) را همینجا ارسال کنید.\n"
        "⚠️ توجه: بازیابی، تنظیمات و درخواست‌های فعلی ربات را بازنویسی می‌کند.",
        reply_markup=CANCEL_INLINE_KEYBOARD,
    )
    return RESTORE_WAIT_FILE


# ---------------------- /setovpn (فقط ادمین) ----------------------
SETOVPN_WAIT_FILE = 310


@admin_only
async def setovpn_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📎 فایل کانفیگ جدید OpenVPN (.ovpn) را ارسال کنید تا جایگزین فایل فعلی شود.\n"
        "این فایل بعد از هر خرید موفق خودکار برای مشتری ارسال می‌شود.",
        reply_markup=CANCEL_INLINE_KEYBOARD,
    )
    return SETOVPN_WAIT_FILE


async def setovpn_get_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.document:
        await update.message.reply_text(
            "❗️ لطفاً فایل را به‌صورت Document ارسال کنید.",
            reply_markup=CANCEL_INLINE_KEYBOARD,
        )
        return SETOVPN_WAIT_FILE

    tg_file = await context.bot.get_file(update.message.document.file_id)
    raw_bytes = bytes(await tg_file.download_as_bytearray())

    with open(OVPN_CONFIG_PATH, "wb") as f:
        f.write(raw_bytes)

    await update.message.reply_text(
        "✅ فایل کانفیگ OpenVPN با موفقیت به‌روزرسانی شد و از این پس برای مشتریان جدید ارسال می‌شود.",
        reply_markup=home_button_keyboard(),
    )
    return ConversationHandler.END


async def restore_get_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.document:
        await update.message.reply_text(
            "❗️ لطفاً فایل بک‌آپ را به‌صورت Document ارسال کنید (نه عکس).",
            reply_markup=CANCEL_INLINE_KEYBOARD,
        )
        return RESTORE_WAIT_FILE

    tg_file = await context.bot.get_file(update.message.document.file_id)
    raw_bytes = bytes(await tg_file.download_as_bytearray())

    try:
        bundle = backup_module.validate_backup(raw_bytes)
    except ValueError as e:
        await update.message.reply_text(
            f"❌ فایل نامعتبر است: {e}\nلطفاً فایل درست را دوباره ارسال کنید.",
            reply_markup=CANCEL_INLINE_KEYBOARD,
        )
        return RESTORE_WAIT_FILE

    context.user_data['restore_bundle'] = bundle

    counts = {
        section: len(bundle["data"].get(section, {}))
        for section in backup_module.STORE_FILES
    }
    summary = "\n".join(f"• {section}: {count} رکورد" for section, count in counts.items())
    if bundle.get("files", {}).get("ovpn_config"):
        summary += "\n• فایل کانفیگ OpenVPN: موجود است ✅"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ بله، بازیابی کن", callback_data="restore_confirm")],
        [InlineKeyboardButton("🚫 لغو", callback_data="settings_cancel")],
    ])
    await update.message.reply_text(
        f"📦 فایل بک‌آپ معتبر است:\n\n{summary}\n\n"
        "⚠️ با تایید، تمام داده‌های فعلی ربات (تنظیمات، درخواست‌های در انتظار، محدودیت‌ها) "
        "با محتوای این فایل جایگزین می‌شود. مطمئن هستید؟",
        reply_markup=keyboard,
    )
    return RESTORE_CONFIRM


async def restore_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    bundle = context.user_data.get('restore_bundle')
    if not bundle:
        await query.edit_message_text("❌ فایل بک‌آپ در دسترس نیست؛ دوباره /restore را بزنید.")
        return ConversationHandler.END

    counts = backup_module.restore_from_bundle(bundle)
    context.user_data.clear()

    summary = "\n".join(f"• {section}: {count} رکورد" for section, count in counts.items())
    await query.edit_message_text(f"✅ بازیابی با موفقیت انجام شد:\n\n{summary}")
    return ConversationHandler.END


async def restore_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لغو در مرحله تایید نهایی ریستور (از دکمه مشترک settings_cancel استفاده می‌کند)"""
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.edit_message_text("❎ بازیابی لغو شد. هیچ داده‌ای تغییر نکرد.")
    return ConversationHandler.END


async def scheduled_backup_job(context: ContextTypes.DEFAULT_TYPE):
    """اجرای دوره‌ای (JobQueue) برای ارسال خودکار بک‌آپ به چت ادمین"""
    if not Config.BACKUP_AUTO_ENABLED:
        return
    try:
        raw = backup_module.create_backup_bytes()
        filename = backup_module.backup_filename()
        await context.bot.send_document(
            chat_id=Config.ADMIN_ID,
            document=raw,
            filename=filename,
            caption="💾 بک‌آپ خودکار دوره‌ای داده‌های ربات.",
        )
    except Exception:
        logger.exception("Scheduled backup failed")


# ---------------------- /stats (فقط ادمین) ----------------------
@admin_only
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total = bot_users.get_total_count()
    recent = bot_users.get_recent(10)

    lines = [
        "📊 آمار کاربران ربات\n",
        f"👥 تعداد کل کاربرانی که /start زده‌اند: {total}\n",
    ]

    if recent:
        lines.append("🆕 آخرین کاربران:")
        for uid, entry in recent:
            dt = datetime.fromtimestamp(entry.get("first_seen", 0))
            lines.append(f"• {entry.get('display_name', '?')} (ID: {uid}) — {dt.strftime('%Y-%m-%d %H:%M')}")

    await _reply(update, context, "\n".join(lines), reply_markup=home_button_keyboard())


# ---------------------- /pending (فقط ادمین) ----------------------
@admin_only
async def pending_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش لیست درخواست‌های در انتظار تایید، با دکمه لغو برای هرکدام (رفع گیر افتادن کاربر)"""
    items = pending_requests.list_pending(Config.PENDING_REQUEST_TIMEOUT_HOURS)

    if not items:
        await update.message.reply_text("📭 هیچ درخواست در انتظار تاییدی وجود ندارد.", reply_markup=home_button_keyboard())
        return

    for request_id, req in items:
        age_hours = (time.time() - req.get("created_at", 0)) / 3600
        text = (
            "🧾 درخواست در انتظار\n\n"
            f"👤 یوزرنیم: {req['username']}\n"
            f"📅 پروفایل: {req['profile']}\n"
            f"💰 مبلغ: {req.get('price') or 'نامشخص'}\n"
            f"💬 مشتری: {req.get('telegram_display')} (ID: {req['telegram_user_id']})\n"
            f"⏱ سن درخواست: {age_hours:.1f} ساعت"
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ تایید و ساخت اکانت", callback_data=f"approve_{request_id}"),
                InlineKeyboardButton("❌ رد", callback_data=f"reject_{request_id}"),
            ],
            [InlineKeyboardButton("🚫 فقط لغو (بدون اطلاع رد)", callback_data=f"cancel_{request_id}")],
        ])
        await update.message.reply_text(text, reply_markup=keyboard)


# ==================================================================
#      تایید / رد رسید توسط ادمین (هندلر مستقل، خارج از Conversation)
# ==================================================================
async def _append_status_to_message(query, addition: str):
    """
    متن وضعیت را چه به caption عکس رسید و چه به متن پیام معمولی (از /pending) اضافه می‌کند.
    """
    if query.message.caption is not None:
        await query.edit_message_caption(caption=query.message.caption + addition)
    else:
        await query.edit_message_text(query.message.text + addition)


async def handle_admin_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != Config.ADMIN_ID:
        await query.answer("⛔️ فقط ادمین می‌تواند این کار را انجام دهد.", show_alert=True)
        return

    action, request_id = query.data.split("_", 1)
    req = pending_requests.get_request(request_id)

    if not req:
        await _append_status_to_message(query, "\n\n⚠️ این درخواست دیگر معتبر نیست یا قبلاً بررسی شده.")
        return

    if req.get("status") != "pending":
        await _append_status_to_message(query, f"\n\n⚠️ این درخواست قبلاً «{req.get('status')}» شده است.")
        return

    if action == "cancel":
        pending_requests.update_status(request_id, "cancelled")
        await _append_status_to_message(query, "\n\n🚫 توسط ادمین لغو شد.")
        try:
            await context.bot.send_message(
                chat_id=req["chat_id"],
                text="🚫 درخواست شما توسط ادمین لغو شد. می‌توانید دوباره با /addvpn تلاش کنید.",
                reply_markup=home_button_keyboard(),
            )
        except Exception:
            logger.exception("Failed to notify customer about cancellation")
        return

    if action == "reject":
        pending_requests.update_status(request_id, "rejected")
        await _append_status_to_message(query, "\n\n❌ رد شد توسط ادمین.")
        try:
            await context.bot.send_message(
                chat_id=req["chat_id"],
                text="❌ رسید پرداخت شما تایید نشد. لطفاً برای پیگیری با پشتیبانی تماس بگیرید یا با /addvpn دوباره تلاش کنید.",
                reply_markup=home_button_keyboard(),
            )
        except Exception:
            logger.exception("Failed to notify customer about rejection")
        return

    if action == "approve":
        await _append_status_to_message(query, "\n\n⏳ در حال ساخت اکانت روی میکروتیک...")
        shared_users = 1 + int(req.get("extra_users", 0) or 0)
        try:
            await asyncio.to_thread(
                mikrotik.add_vpn_user, req["username"], req["password"], req["profile"], shared_users
            )
            pending_requests.update_status(request_id, "approved")
            rate_limit.register_request(req["telegram_user_id"])
            customer_accounts.record_purchase(
                req["telegram_user_id"], req["username"], req["profile"], shared_users
            )

            await _append_status_to_message(query, "\n\n✅ تایید شد و اکانت ساخته شد.")

            await context.bot.send_message(
                chat_id=req["chat_id"],
                text=_create_vpn_account_text(req["username"], req["password"], req["profile"], shared_users),
                reply_markup=home_button_keyboard(),
            )
            await send_purchase_tutorial(context, req["chat_id"], req["username"], req["password"])
        except DuplicateUserError as e:
            await _append_status_to_message(query, f"\n\n⚠️ خطا: {e}")
        except MikrotikError as e:
            await _append_status_to_message(query, f"\n\n❌ خطا: {e}")
        except Exception as e:
            logger.exception("Unexpected error approving request")
            await _append_status_to_message(query, f"\n\n❌ خطای غیرمنتظره: {e}")


# ==================================================================
#                        /delvpn Conversation
# ==================================================================
@admin_only
async def delvpn_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _reply(
        update, context,
        "🗑 نام کاربری‌ای که می‌خواهید حذف شود را وارد کنید:",
        reply_markup=CANCEL_INLINE_KEYBOARD,
    )
    return DEL_USERNAME


async def delvpn_get_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.text.strip()
    await update.message.reply_text(f"⏳ در حال حذف یوزر «{username}»...")

    try:
        await asyncio.to_thread(mikrotik.delete_vpn_user, username)
        await update.message.reply_text(f"✅ یوزر «{username}» با موفقیت حذف شد.", reply_markup=home_button_keyboard())
    except UserNotFoundError as e:
        await update.message.reply_text(f"⚠️ {e}", reply_markup=home_button_keyboard())
    except MikrotikError as e:
        await update.message.reply_text(f"❌ خطا: {e}", reply_markup=home_button_keyboard())
    except Exception as e:
        logger.exception("Unexpected error in delvpn_get_username")
        await update.message.reply_text(f"❌ خطای غیرمنتظره: {e}", reply_markup=home_button_keyboard())

    return ConversationHandler.END


# ==================================================================
#                        /listvpn
# ==================================================================
@admin_only
async def listvpn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.callback_query:
        await update.callback_query.answer()

    await context.bot.send_message(chat_id=chat_id, text="⏳ در حال دریافت لیست یوزرها از میکروتیک...")
    try:
        users = await asyncio.to_thread(mikrotik.list_vpn_users)
        if not users:
            await context.bot.send_message(
                chat_id=chat_id, text="📭 هیچ یوزری در User Manager یافت نشد.",
                reply_markup=home_button_keyboard(),
            )
            return

        # نگاشت وضعیت واقعی User Manager به ایموجی قابل فهم
        STATE_ICONS = {
            "running-active": "🟢 فعال",
            "used": "⚪ استفاده‌شده (قبلی)",
            "expired": "🔴 منقضی",
            "not-active": "🟡 غیرفعال",
        }

        lines = ["📋 لیست یوزرهای VPN:\n"]
        for u in users:
            status = STATE_ICONS.get(u.get('state'), f"❔ {u.get('state', 'نامشخص')}")
            lines.append(f"👤 {u['name']} | پروفایل: {u['profile']} | {status}")

        text = "\n".join(lines)
        chunk_size = 3500
        if len(text) > chunk_size:
            chunks = [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]
            for i, chunk in enumerate(chunks):
                is_last = (i == len(chunks) - 1)
                await context.bot.send_message(
                    chat_id=chat_id, text=chunk,
                    reply_markup=home_button_keyboard() if is_last else None,
                )
        else:
            await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=home_button_keyboard())

    except MikrotikError as e:
        await context.bot.send_message(chat_id=chat_id, text=f"❌ خطا: {e}", reply_markup=home_button_keyboard())
    except Exception as e:
        logger.exception("Unexpected error in listvpn")
        await context.bot.send_message(chat_id=chat_id, text=f"❌ خطای غیرمنتظره: {e}", reply_markup=home_button_keyboard())


# ---------------------- /cancel ----------------------
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    if update.message:
        cleanup_msg = await update.message.reply_text("⌨️", reply_markup=ReplyKeyboardRemove())
        try:
            await cleanup_msg.delete()
        except Exception:
            pass
    await _reply(update, context, "❎ عملیات لغو شد.", reply_markup=home_button_keyboard())
    return ConversationHandler.END


# ---------------------- هندلر خطای سراسری ----------------------
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error: {context.error}")


def main():
    if not Config.BOT_TOKEN:
        raise SystemExit("❌ BOT_TOKEN در فایل .env تعریف نشده است.")
    if not Config.ADMIN_ID:
        raise SystemExit("❌ ADMIN_ID در فایل .env تعریف نشده است.")

    application = Application.builder().token(Config.BOT_TOKEN).build()

    add_conv = ConversationHandler(
        entry_points=[
            CommandHandler("addvpn", addvpn_start),
            CallbackQueryHandler(addvpn_start, pattern=f"^{MENU_BUY_CB}$"),
        ],
        states={
            ADD_USERNAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, addvpn_get_username),
                CallbackQueryHandler(flow_cancel_callback, pattern="^flow_cancel$"),
            ],
            ADD_PASSWORD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, addvpn_get_password),
                CallbackQueryHandler(flow_cancel_callback, pattern="^flow_cancel$"),
            ],
            ADD_PROFILE: [
                CallbackQueryHandler(addvpn_get_profile, pattern="^profile_"),
                CallbackQueryHandler(flow_cancel_callback, pattern="^flow_cancel$"),
            ],
            ADD_EXTRA_USERS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, addvpn_get_extra_users),
                CallbackQueryHandler(flow_cancel_callback, pattern="^flow_cancel$"),
            ],
            ADD_RECEIPT: [
                MessageHandler(filters.PHOTO, addvpn_get_receipt),
                CallbackQueryHandler(flow_cancel_callback, pattern="^flow_cancel$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    del_conv = ConversationHandler(
        entry_points=[
            CommandHandler("delvpn", delvpn_start),
            CallbackQueryHandler(delvpn_start, pattern=f"^{MENU_DELETE_CB}$"),
        ],
        states={
            DEL_USERNAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, delvpn_get_username),
                CallbackQueryHandler(flow_cancel_callback, pattern="^flow_cancel$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    settings_conv = ConversationHandler(
        entry_points=[
            CommandHandler("settings", settings_start),
            CallbackQueryHandler(settings_start, pattern=f"^{MENU_MANAGE_CB}$"),
        ],
        states={
            SETTINGS_FIELD_SELECT: [
                CallbackQueryHandler(settings_field_chosen, pattern="^setkey_"),
                CallbackQueryHandler(settings_close, pattern="^settings_close$"),
            ],
            SETTINGS_WAIT_VALUE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, settings_save_value),
                CallbackQueryHandler(settings_toggle_bool, pattern="^boolval_(yes|no)$"),
                CallbackQueryHandler(settings_cancel_value, pattern="^settings_cancel$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    restore_conv = ConversationHandler(
        entry_points=[CommandHandler("restore", restore_start)],
        states={
            RESTORE_WAIT_FILE: [
                MessageHandler(filters.Document.ALL, restore_get_file),
                CallbackQueryHandler(flow_cancel_callback, pattern="^flow_cancel$"),
            ],
            RESTORE_CONFIRM: [
                CallbackQueryHandler(restore_confirmed, pattern="^restore_confirm$"),
                CallbackQueryHandler(restore_cancel_callback, pattern="^settings_cancel$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    setovpn_conv = ConversationHandler(
        entry_points=[CommandHandler("setovpn", setovpn_start)],
        states={
            SETOVPN_WAIT_FILE: [
                MessageHandler(filters.Document.ALL, setovpn_get_file),
                CallbackQueryHandler(flow_cancel_callback, pattern="^flow_cancel$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(add_conv)
    application.add_handler(del_conv)
    application.add_handler(settings_conv)
    application.add_handler(restore_conv)
    application.add_handler(setovpn_conv)
    application.add_handler(CommandHandler("listvpn", listvpn))
    application.add_handler(CommandHandler("pending", pending_list))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CallbackQueryHandler(stats_command, pattern=f"^{MENU_STATS_CB}$"))
    application.add_handler(CommandHandler("backup", backup_now))
    application.add_handler(CallbackQueryHandler(listvpn, pattern=f"^{MENU_LIST_CB}$"))
    application.add_handler(CallbackQueryHandler(support_handler, pattern=f"^{MENU_SUPPORT_CB}$"))
    application.add_handler(CallbackQueryHandler(menu_home_callback, pattern=f"^{MENU_HOME_CB}$"))
    application.add_handler(CallbackQueryHandler(menu_tutorial_callback, pattern=f"^{MENU_TUTORIAL_CB}$"))
    application.add_handler(
        CallbackQueryHandler(tutorial_protocol_callback, pattern=f"^({TUTORIAL_L2TP_CB}|{TUTORIAL_OVPN_CB})$")
    )
    application.add_handler(CommandHandler("tutorial", menu_tutorial_callback))
    application.add_handler(CallbackQueryHandler(my_accounts_callback, pattern=f"^{MENU_MY_ACCOUNTS_CB}$"))
    application.add_handler(CommandHandler("myaccounts", my_accounts_callback))
    # هندلر مستقل تایید/رد/لغو رسید توسط ادمین (خارج از ConversationHandler چون در چت جدای ادمین اتفاق می‌افتد)
    application.add_handler(CallbackQueryHandler(handle_admin_decision, pattern="^(approve|reject|cancel)_"))
    application.add_error_handler(error_handler)

    # ---------------- بک‌آپ خودکار دوره‌ای ----------------
    if application.job_queue is not None and Config.BACKUP_AUTO_ENABLED:
        interval_seconds = max(Config.BACKUP_INTERVAL_HOURS, 1) * 3600
        application.job_queue.run_repeating(
            scheduled_backup_job,
            interval=interval_seconds,
            first=60,  # اولین بک‌آپ ۱ دقیقه بعد از بالا آمدن ربات
            name="auto_backup",
        )
        logger.info(f"بک‌آپ خودکار هر {Config.BACKUP_INTERVAL_HOURS} ساعت فعال شد.")

    logger.info("ربات در حال اجراست (polling)...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
