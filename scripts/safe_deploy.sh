#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="/opt/kleinanzeigen-order-tool"
SERVICE="kleinanzeigen-order-tool.service"
BACKUP_DIR="/root/kleinanzeigen-order-tool-backups"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_FILE="$BACKUP_DIR/code-$TIMESTAMP.tar.gz"
OLD_COMMIT=""

cd "$APP_DIR"
mkdir -p "$BACKUP_DIR"
OLD_COMMIT="$(git rev-parse HEAD)"

tar --exclude='.git' --exclude='.env' --exclude='venv' --exclude='__pycache__' --exclude='orders.db' \
  -czf "$BACKUP_FILE" .

echo "Backup: $BACKUP_FILE"
git fetch origin main
git merge --ff-only origin/main
./venv/bin/pip install -r requirements.txt
./venv/bin/python -m py_compile app.py wsgi.py
systemctl restart "$SERVICE"
sleep 2

if ! systemctl is-active --quiet "$SERVICE"; then
  echo "Dienststart fehlgeschlagen. Rollback auf $OLD_COMMIT"
  git reset --hard "$OLD_COMMIT"
  ./venv/bin/pip install -r requirements.txt
  systemctl restart "$SERVICE"
  systemctl status "$SERVICE" --no-pager
  exit 1
fi

systemctl status "$SERVICE" --no-pager
journalctl -u "$SERVICE" -n 40 --no-pager
