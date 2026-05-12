#!/bin/bash
# ============================================
# DEPLOY SCRIPT — Crypto Bias Bot
# Jalankan di VPS setelah file ter-upload
# ============================================
# PACKAGING (jalankan di lokal sebelum upload):
#   zip -r crypto_bot.zip pkg_final/ \
#       --exclude "pkg_final/__pycache__/*" \
#       --exclude "pkg_final/*.pyc" \
#       --exclude "pkg_final/.env" \
#       --exclude "pkg_final/*.db" \
#       --exclude "pkg_final/risk_state.json" \
#       --exclude "pkg_final/settings.json"
# Salin .env secara manual ke VPS — JANGAN masukkan ke archive.
# ============================================

set -e

echo "📦 Installing Python & Dependencies..."
apt update -y && apt install -y python3 python3-pip python3-venv

echo "📁 Setting up project..."
cd /root/crypto_bias_bot
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt   # gunakan requirements.txt, bukan daftar manual

echo "🔧 Creating systemd service..."
cat > /etc/systemd/system/cryptobot.service << 'EOF'
[Unit]
Description=Crypto Bias Signal Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/crypto_bias_bot
ExecStart=/root/crypto_bias_bot/venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable cryptobot
systemctl start cryptobot

echo "✅ Bot deployed and running!"
echo "📡 Check status: systemctl status cryptobot"
echo "📋 View logs: journalctl -u cryptobot -f"
