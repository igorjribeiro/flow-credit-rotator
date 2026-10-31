#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_TARGET="${HOME}/.local/bin"
DB_DIR="${HOME}/.flow-accounts"

echo "=========================================================="
echo "Installing Flow Credit Rotator"
echo "=========================================================="

# Ensure Python 3 is available
if ! command -v python3 &>/dev/null; then
    echo "Error: Python 3 is required but not installed." >&2
    exit 1
fi

# Ensure ~/.local/bin and ~/.flow-accounts exist
mkdir -p "${BIN_TARGET}"
mkdir -p "${DB_DIR}"

# Make launcher executable
chmod +x "${SCRIPT_DIR}/bin/flow-account"

# Install or link binary into ~/.local/bin
TARGET_LINK="${BIN_TARGET}/flow-account"
if [ -L "${TARGET_LINK}" ] || [ -f "${TARGET_LINK}" ]; then
    rm -f "${TARGET_LINK}"
fi
ln -s "${SCRIPT_DIR}/bin/flow-account" "${TARGET_LINK}"
echo "✓ Symlinked flow-account to ${TARGET_LINK}"

# Install package in editable mode if pip is available
if command -v pip3 &>/dev/null; then
    pip3 install -e "${SCRIPT_DIR}" --no-deps 2>/dev/null || true
elif command -v pip &>/dev/null; then
    pip install -e "${SCRIPT_DIR}" --no-deps 2>/dev/null || true
fi

# Register skill for Hermes / Claude Code if directory exists
HERMES_SKILLS_DIR="${HOME}/.hermes/skills"
if [ -d "${HERMES_SKILLS_DIR}" ]; then
    OLD_SKILL_LINK="${HERMES_SKILLS_DIR}/google-flow-account-manager"
    if [ -L "${OLD_SKILL_LINK}" ] || [ -e "${OLD_SKILL_LINK}" ]; then
        rm -rf "${OLD_SKILL_LINK}"
        echo "✓ Removed legacy skill link: ${OLD_SKILL_LINK}"
    fi

    SKILL_LINK="${HERMES_SKILLS_DIR}/flow-credit-rotator"
    rm -rf "${SKILL_LINK}"
    ln -s "${SCRIPT_DIR}" "${SKILL_LINK}"
    echo "✓ Registered skill with Hermes at ${SKILL_LINK}"
fi

echo ""
echo "Installation successful!"
echo "Check installation with:"
echo "  flow-account status"
echo "Make sure ${BIN_TARGET} is in your PATH."
