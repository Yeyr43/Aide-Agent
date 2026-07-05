#!/usr/bin/env bash
# Aide Agent — 一键安装脚本 (macOS/Linux 源码版)
# 用法:
#   curl -fsSL https://raw.githubusercontent.com/Yeyr43/Aide-Agent/main/install.sh | bash
#   bash install.sh            # 本地运行
#   bash install.sh --uninstall
set -euo pipefail

REPO_URL="https://github.com/Yeyr43/Aide-Agent.git"
INSTALL_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/aide-agent"
BIN_DIR="$HOME/.local/bin"
BRANCH="${AIDE_BRANCH:-main}"

# ── 卸载 ──────────────────────────────────────────────
if [ "${1:-}" = "--uninstall" ] || [ "${1:-}" = "-Uninstall" ]; then
    echo "Uninstalling Aide Agent..."
    rm -f "$BIN_DIR/aide"
    rm -rf "$INSTALL_DIR"
    echo "Done."
    exit 0
fi

echo "=== Aide Agent Installer ==="

# ── 前置检查 ──────────────────────────────────────────
if ! command -v git &>/dev/null; then
    echo "Error: git not found. Install: https://git-scm.com" >&2
    exit 1
fi
echo "[OK] git: $(git --version | head -1)"

if ! command -v uv &>/dev/null; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.cargo/bin:$PATH"
fi
echo "[OK] uv: $(uv --version)"

# ── Clone / Update ────────────────────────────────────
if [ -d "$INSTALL_DIR/.git" ]; then
    echo "Updating existing install at $INSTALL_DIR..."
    git -C "$INSTALL_DIR" fetch origin
    git -C "$INSTALL_DIR" checkout "$BRANCH"
    git -C "$INSTALL_DIR" pull origin "$BRANCH"
else
    echo "Cloning to $INSTALL_DIR..."
    git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi

# ── Install dependencies ─────────────────────────────
echo "Installing dependencies (uv sync)..."
(cd "$INSTALL_DIR" && uv sync)

# ── Create launcher symlink ──────────────────────────
mkdir -p "$BIN_DIR"
ln -sf "$INSTALL_DIR/aide" "$BIN_DIR/aide"
chmod +x "$INSTALL_DIR/aide"
echo "[OK] Linked: $BIN_DIR/aide -> $INSTALL_DIR/aide"

# ── Ensure ~/.local/bin is in PATH ───────────────────
if ! echo "$PATH" | tr ':' '\n' | grep -qxF "$BIN_DIR"; then
    SHELL_NAME="$(basename "$SHELL")"
    case "$SHELL_NAME" in
        zsh)  PROFILE="$HOME/.zshrc" ;;
        bash) PROFILE="$HOME/.bashrc" ;;
        fish) PROFILE="$HOME/.config/fish/config.fish" ;;
        *)    PROFILE="$HOME/.profile" ;;
    esac
    echo "export PATH=\"$BIN_DIR:\$PATH\"" >> "$PROFILE"
    echo "[OK] Added $BIN_DIR to PATH in $PROFILE"
fi

echo ""
echo "Aide Agent installed! Restart terminal and type 'aide' to start."
echo "Uninstall: bash $INSTALL_DIR/install.sh --uninstall"
