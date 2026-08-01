#!/bin/bash
# ==============================================================
# install.sh
# نصب خودکار ربات مدیریت VPN میکروتیک (bot-mikrotik)
#
# فقط دو مقدار از شما می‌پرسد: BOT_TOKEN و ADMIN_ID.
# بقیه تنظیمات (اطلاعات میکروتیک، شماره کارت، قیمت‌ها و ...) بعداً از
# داخل خود ربات با دستور /settings قابل تنظیم است.
#
# استفاده:
#   git clone https://github.com/radmehr-rzj/bot-mikrotik.git
#   cd bot-mikrotik
#   sudo bash install.sh
# ==============================================================

set -e

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="mikrotik-vpn-bot"

echo "=============================================="
echo " نصب ربات مدیریت VPN میکروتیک"
echo " مسیر نصب: $INSTALL_DIR"
echo "=============================================="
echo ""

# ---------------- بررسی دسترسی root ----------------
if [ "$EUID" -ne 0 ]; then
    echo "❌ لطفاً این اسکریپت را با sudo یا کاربر root اجرا کنید:"
    echo "   sudo bash install.sh"
    exit 1
fi

# ---------------- نصب پیش‌نیازهای سیستمی ----------------
echo "▶ بررسی و نصب پیش‌نیازهای سیستمی (python3, pip, venv)..."
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip >/dev/null
echo "✅ پیش‌نیازهای سیستمی آماده است."
echo ""

# ---------------- ساخت virtualenv و نصب پکیج‌ها ----------------
if [ ! -d "$INSTALL_DIR/venv" ]; then
    echo "▶ ساخت محیط مجازی پایتون..."
    python3 -m venv "$INSTALL_DIR/venv"
fi

echo "▶ نصب کتابخانه‌های پایتون (ممکن است کمی طول بکشد)..."
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip -q
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt" -q
echo "✅ کتابخانه‌ها نصب شدند."
echo ""

# ---------------- ساخت فایل .env (فقط اگر از قبل وجود نداشته باشد) ----------------
ENV_FILE="$INSTALL_DIR/.env"

if [ -f "$ENV_FILE" ]; then
    echo "ℹ️  فایل .env از قبل وجود دارد؛ از آن استفاده می‌شود (تغییری داده نمی‌شود)."
else
    echo "▶ تنظیمات اولیه ربات را وارد کنید:"
    echo ""
    read -rp "🔑 توکن ربات تلگرام (از @BotFather بگیرید): " BOT_TOKEN
    while [ -z "$BOT_TOKEN" ]; do
        read -rp "   توکن نمی‌تواند خالی باشد. دوباره وارد کنید: " BOT_TOKEN
    done

    read -rp "🆔 آیدی عددی تلگرام شما به‌عنوان ادمین (از @userinfobot بگیرید): " ADMIN_ID
    while ! [[ "$ADMIN_ID" =~ ^[0-9]+$ ]]; do
        read -rp "   آیدی باید فقط عدد باشد. دوباره وارد کنید: " ADMIN_ID
    done

    cat > "$ENV_FILE" << EOF
BOT_TOKEN=$BOT_TOKEN
ADMIN_ID=$ADMIN_ID
EOF
    echo ""
    echo "✅ فایل .env ساخته شد."
fi
echo ""

# ---------------- ساخت سرویس systemd ----------------
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

echo "▶ ساخت سرویس systemd..."
cat > "$SERVICE_FILE" << EOF
[Unit]
Description=Mikrotik VPN Telegram Bot
After=network.target

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/bot.py
Restart=on-failure
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null
systemctl restart "$SERVICE_NAME"

sleep 2
echo ""
echo "=============================================="
if systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "✅ ربات با موفقیت نصب و اجرا شد!"
else
    echo "⚠️ سرویس بالا نیامد. برای بررسی خطا:"
    echo "   journalctl -u $SERVICE_NAME -n 50 --no-pager"
fi
echo "=============================================="
echo ""
echo "مراحل بعدی:"
echo "  ۱. در تلگرام به ربات خودتان بروید و /start را بزنید."
echo "  ۲. با دستور /settings اطلاعات میکروتیک (IP، یوزر API، پسورد)،"
echo "     شماره کارت، قیمت‌ها و بقیه تنظیمات را وارد کنید."
echo ""
echo "دستورات مفید:"
echo "  وضعیت سرویس:   systemctl status $SERVICE_NAME"
echo "  لاگ زنده:       journalctl -u $SERVICE_NAME -f"
echo "  ری‌استارت:      systemctl restart $SERVICE_NAME"
echo ""
