#!/bin/bash
set -euo pipefail

# Systemd User Delegation Setup for Arch Linux

TARGET_USER="${1:-${SUDO_USER:-$USER}}"

echo "=== Systemd User Delegation Setup ==="
echo "Target user: $TARGET_USER"
echo

if [[ $EUID -ne 0 ]]; then
    echo "Run as root (or with sudo)"
    exit 1
fi

# Create systemd drop-in for delegation
echo "[1/3] Creating systemd delegation drop-in..."
mkdir -p /etc/systemd/system/user@.service.d/
cat > /etc/systemd/system/user@.service.d/delegate.conf <<EOF
[Service]
Delegate=yes
EOF
echo "  ✓ Created /etc/systemd/system/user@.service.d/delegate.conf"

# Enable lingering
echo "[2/3] Enabling user lingering..."
loginctl enable-linger "$TARGET_USER"
echo "  ✓ Lingering enabled for $TARGET_USER"

# Reload systemd
echo "[3/3] Reloading systemd..."
systemctl daemon-reload
echo "  ✓ Systemd reloaded"

echo
echo "=== Setup Complete ==="
echo "Log out and back in, then verify with:"
echo "  cat /sys/fs/cgroup/user.slice/user-\$(id -u).slice/user@\$(id -u).service/cgroup.controllers"
