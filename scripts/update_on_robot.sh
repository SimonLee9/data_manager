#!/usr/bin/env bash
# update_on_robot.sh — pull the latest sn2_backup code from GitHub and
# reinstall it on this robot, preserving credentials/config/state.
#
# Usage on the robot:
#   bash ~/ws/data_manager/scripts/update_on_robot.sh
#
# What this PRESERVES:
#   ~/.sn2_backup/credentials.json
#   ~/.sn2_backup/token.json
#   ~/.sn2_backup/env             (SMTP app password)
#   ~/.sn2_backup/config.yaml     (your robot_id, settings)
#   ~/.sn2_backup/state.json      (uploaded-files history)
#
# What this REPLACES:
#   ~/ws/data_manager/            (old → ~/ws/data_manager.bak.<timestamp>)
#   ~/.sn2_backup/venv/           (recreated to pick up new dependencies)
#
# Override the repo via env vars if you forked:
#   SN2_REPO_URL=https://github.com/me/data_manager.git \
#   SN2_REPO_BRANCH=main \
#   bash scripts/update_on_robot.sh

set -euo pipefail

REPO_URL="${SN2_REPO_URL:-https://github.com/SimonLee9/data_manager.git}"
REPO_BRANCH="${SN2_REPO_BRANCH:-main}"
REPO_DIR="$HOME/ws/data_manager"
SN2_DIR="$HOME/.sn2_backup"
VENV_DIR="$SN2_DIR/venv"

if [[ -t 1 ]]; then
    GREEN='\033[0;32m'; YELLOW='\033[0;33m'; RED='\033[0;31m'; BOLD='\033[1m'; NC='\033[0m'
else
    GREEN=''; YELLOW=''; RED=''; BOLD=''; NC=''
fi
info() { printf "${GREEN}[ ok ]${NC} %s\n" "$*"; }
warn() { printf "${YELLOW}[warn]${NC} %s\n" "$*"; }
err()  { printf "${RED}[FAIL]${NC} %s\n" "$*" >&2; }
step() { printf "\n${BOLD}==> %s${NC}\n" "$*"; }

if [[ "$EUID" -eq 0 ]]; then
    err "do not run as root"
    exit 1
fi

require_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        err "missing required command: $1"
        return 1
    fi
}

step "Pre-flight"
MISSING=0
require_cmd git || MISSING=1
require_cmd python3 || MISSING=1
[[ $MISSING -eq 0 ]] || {
    err "install missing tools first (e.g. sudo apt install -y git)"
    exit 1
}

# Sanity: credentials/secrets must already be present (this is an UPDATE, not
# a first-time install).
if [[ ! -f "$SN2_DIR/credentials.json" ]] || [[ ! -f "$SN2_DIR/token.json" ]] || [[ ! -f "$SN2_DIR/env" ]]; then
    err "this looks like a first-time install (credentials/token/env missing)."
    err "  use sn2_install.sh from build_bundle.sh instead."
    exit 1
fi
info "credentials, token, env present"

# Show what's preserved
step "Preserving"
for f in credentials.json token.json env config.yaml state.json; do
    if [[ -e "$SN2_DIR/$f" ]]; then
        info "$SN2_DIR/$f"
    fi
done

# Clone latest into a temp dir so a network/git failure can't damage the
# existing install.
TMPDIR=$(mktemp -d -t sn2_update.XXXXXX)
trap 'rm -rf "$TMPDIR"' EXIT

step "Cloning $REPO_URL ($REPO_BRANCH)"
if ! git clone --depth 1 --branch "$REPO_BRANCH" "$REPO_URL" "$TMPDIR/repo"; then
    err "git clone failed — check network and SN2_REPO_URL"
    exit 1
fi
HEAD_SHA=$(git -C "$TMPDIR/repo" rev-parse --short HEAD)
HEAD_MSG=$(git -C "$TMPDIR/repo" log -1 --pretty=%s)
info "fetched: $HEAD_SHA  $HEAD_MSG"

# Atomic-ish swap of repo dir
step "Swapping in new repo at $REPO_DIR"
if [[ -d "$REPO_DIR" ]]; then
    BAK="${REPO_DIR}.bak.$(date +%s)"
    mv "$REPO_DIR" "$BAK"
    info "backed up old repo to $BAK"
fi
mkdir -p "$(dirname "$REPO_DIR")"
mv "$TMPDIR/repo" "$REPO_DIR"
info "new repo in place"

step "Recreating venv"
rm -rf "$VENV_DIR"
info "removed $VENV_DIR (install_on_robot.sh will rebuild)"

step "Handing off to install_on_robot.sh"
exec bash "$REPO_DIR/scripts/install_on_robot.sh"
