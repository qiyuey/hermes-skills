#!/usr/bin/env bash
# Install the WireGuard wg0 LaunchDaemon on macOS.
#   sudo bash install.sh
#
# Idempotent: re-running just refreshes the files. Does NOT start the service.
# Skill: ~/Code/hermes-skills/skills/wireguard/SKILL.md
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
    echo "must run as root: sudo bash $0" >&2
    exit 1
fi

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="top.qiyuey.wireguard.wg0"

# Resolve the invoking user's home, not root's, so we can find the user's
# XDG config directory when running under `sudo bash install.sh`.
INVOKER_HOME="$(eval echo "~${SUDO_USER:-$(logname 2>/dev/null || echo "$USER")}")"
XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-${INVOKER_HOME}/.config}"

# Canonical wg0.conf location. Override with WG_CONF_SRC=/path/to/wg0.conf.
WG_CONF_SRC="${WG_CONF_SRC:-${XDG_CONFIG_HOME}/wireguard/wg0.conf}"
RUNNER_SRC="${SRC_DIR}/wg-svc-runner"
PLIST_SRC="${SRC_DIR}/${LABEL}.plist"

WG_CONF_DST_DIR="/opt/homebrew/etc/wireguard"
WG_CONF_DST="${WG_CONF_DST_DIR}/wg0.conf"
RUNNER_DST_DIR="/usr/local/libexec"
RUNNER_DST="${RUNNER_DST_DIR}/wg-svc-runner"
PLIST_DST="/Library/LaunchDaemons/${LABEL}.plist"
LOG_DST="/var/log/wireguard-wg0.log"

if [[ ! -f "${WG_CONF_SRC}" ]]; then
    cat >&2 <<EOF
[install] wg0.conf not found at:
    ${WG_CONF_SRC}

Create it from the template (one-time setup, NOT as root):

    mkdir -p "\${XDG_CONFIG_HOME:-\$HOME/.config}/wireguard"
    chmod 700 "\${XDG_CONFIG_HOME:-\$HOME/.config}/wireguard"
    install -m 0600 "${SRC_DIR}/wg0.conf.example" \\
        "\${XDG_CONFIG_HOME:-\$HOME/.config}/wireguard/wg0.conf"
    \$EDITOR "\${XDG_CONFIG_HOME:-\$HOME/.config}/wireguard/wg0.conf"

Then rerun:  sudo bash $0

(Or point at a custom path: sudo WG_CONF_SRC=/path/to/wg0.conf bash $0)
EOF
    exit 2
fi
for f in "${RUNNER_SRC}" "${PLIST_SRC}"; do
    [[ -f "$f" ]] || { echo "missing source: $f" >&2; exit 2; }
done

echo "[install] plist lint"
/usr/bin/plutil -lint "${PLIST_SRC}" >/dev/null

echo "[install] wg0.conf -> ${WG_CONF_DST}"
install -d -m 0755 -o root -g wheel "${WG_CONF_DST_DIR}"
install -m 0600 -o root -g wheel "${WG_CONF_SRC}" "${WG_CONF_DST}"

echo "[install] wg-svc-runner -> ${RUNNER_DST}"
install -d -m 0755 -o root -g wheel "${RUNNER_DST_DIR}"
install -m 0755 -o root -g wheel "${RUNNER_SRC}" "${RUNNER_DST}"

echo "[install] ${LABEL}.plist -> ${PLIST_DST}"
install -m 0644 -o root -g wheel "${PLIST_SRC}" "${PLIST_DST}"

if [[ ! -e "${LOG_DST}" ]]; then
    echo "[install] create ${LOG_DST}"
    install -m 0644 -o root -g wheel /dev/null "${LOG_DST}"
fi

echo
echo "[install] done — service installed but NOT started."
echo
echo "next:"
echo "  1. quit the official WireGuard.app (avoid 192.168.10.3 conflict)"
echo "  2. sudo launchctl bootstrap system ${PLIST_DST}"
echo "  3. sudo launchctl print system/${LABEL} | awk '/state =|pid =/'"
echo "  4. sudo wg show"
