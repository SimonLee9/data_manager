#!/usr/bin/env bash
# install_on_robot.sh — set up sn2_backup on the robot and start the systemd timer.
#
# Usage (on the robot):
#   git clone <repo-url> ~/ws/data_manager
#   cd ~/ws/data_manager
#   bash scripts/install_on_robot.sh
#
# Pass --check to validate the existing install without changing anything.
# Pass --no-systemd to do everything except installing/starting systemd units
# (useful for testing on a laptop).

set -euo pipefail

CHECK_ONLY=0
NO_SYSTEMD=0
for arg in "$@"; do
    case "$arg" in
        --check)       CHECK_ONLY=1 ;;
        --no-systemd)  NO_SYSTEMD=1 ;;
        -h|--help)
            sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'
            exit 0
            ;;
        *)
            echo "unknown argument: $arg" >&2
            exit 2
            ;;
    esac
done

# ---------- paths ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SN2_DIR="$HOME/.sn2_backup"
VENV_DIR="$SN2_DIR/venv"
CONFIG_PATH="$SN2_DIR/config.yaml"
CREDS_PATH="$SN2_DIR/credentials.json"
TOKEN_PATH="$SN2_DIR/token.json"
ENV_PATH="$SN2_DIR/env"
SYSTEMD_DIR="/etc/systemd/system"

# ---------- output helpers ----------
if [[ -t 1 ]]; then
    GREEN='\033[0;32m'; YELLOW='\033[0;33m'; RED='\033[0;31m'; BOLD='\033[1m'; NC='\033[0m'
else
    GREEN=''; YELLOW=''; RED=''; BOLD=''; NC=''
fi
info()  { printf "${GREEN}[ ok ]${NC} %b\n" "$*"; }
warn()  { printf "${YELLOW}[warn]${NC} %b\n" "$*"; }
err()   { printf "${RED}[FAIL]${NC} %b\n" "$*" >&2; }
step()  { printf "\n${BOLD}==> %s${NC}\n" "$*"; }

# ---------- pre-flight ----------
if [[ "$EUID" -eq 0 ]]; then
    err "do not run as root — run as the user that owns ~/.sn2_backup"
    exit 1
fi

require_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        err "missing required command: $1"
        return 1
    fi
}

step "Pre-flight checks"
MISSING=0
require_cmd python3 || MISSING=1
if [[ $NO_SYSTEMD -eq 0 ]]; then
    require_cmd systemctl || MISSING=1
    require_cmd sudo      || MISSING=1
fi
if ! python3 -c "import venv, ensurepip" >/dev/null 2>&1; then
    err "python3 venv/ensurepip missing — install with: sudo apt install -y python3-venv"
    MISSING=1
fi
[[ $MISSING -eq 0 ]] || exit 1
info "host has python3, venv module, and systemctl"

info "repo:      $REPO_ROOT"
info "install:   $SN2_DIR"
info "venv:      $VENV_DIR"
info "config:    $CONFIG_PATH"

# ---------- credentials gating ----------
step "Credential & secret files"

check_file() {
    local p="$1" desc="$2" hint="$3"
    if [[ -e "$p" ]]; then
        info "$desc present: $p"
        chmod 600 "$p" 2>/dev/null || true
        return 0
    else
        err "$desc missing: $p"
        printf "       %s\n" "$hint" >&2
        return 1
    fi
}

# Interactive prompt for the Gmail app password — only when stdin is a TTY and
# we're not in --check mode. The user pastes the 16-char app password (with or
# without the spaces Google shows). We strip spaces, validate, and write the
# env file with mode 600.
prompt_for_app_password() {
    if [[ ! -t 0 ]]; then
        return 1
    fi
    echo
    printf "${BOLD}Gmail app password setup${NC}\n"
    printf "  Generate one at: https://myaccount.google.com/apppasswords\n"
    printf "  Paste it below (with or without spaces; input is hidden).\n"
    echo

    mkdir -p "$SN2_DIR"
    chmod 700 "$SN2_DIR"

    local pw cleaned
    for attempt in 1 2 3; do
        read -rsp "  app password> " pw
        echo
        cleaned="${pw// /}"  # remove spaces from "xxxx xxxx xxxx xxxx" form
        if [[ ${#cleaned} -ne 16 ]] || [[ ! "$cleaned" =~ ^[a-zA-Z0-9]+$ ]]; then
            warn "expected 16 alphanumeric characters (got ${#cleaned}); try again"
            continue
        fi
        umask 077
        printf 'SN2_BACKUP_APP_PASSWORD=%s\n' "$cleaned" > "$ENV_PATH"
        chmod 600 "$ENV_PATH"
        info "wrote $ENV_PATH"
        return 0
    done
    err "too many failed attempts entering app password"
    return 1
}

CRED_OK=0
check_file "$CREDS_PATH" "OAuth credentials" \
    "Generate on your laptop via Google Cloud Console → Drive API → OAuth client (Desktop), then SCP here." \
    && CRED_OK=$((CRED_OK + 1))
check_file "$TOKEN_PATH" "OAuth token" \
    "On your laptop run: python scripts/authorize_drive.py credentials.json token.json   then SCP token.json here." \
    && CRED_OK=$((CRED_OK + 1))

# Env file: try interactive prompt before declaring failure (skip when --check).
if [[ ! -e "$ENV_PATH" ]] && [[ $CHECK_ONLY -eq 0 ]]; then
    if prompt_for_app_password; then
        :
    else
        err "SMTP env file missing: $ENV_PATH"
        printf "       %s\n" "Create it manually: printf 'SN2_BACKUP_APP_PASSWORD=<16-char>\\n' > $ENV_PATH && chmod 600 $ENV_PATH" >&2
    fi
fi

check_file "$ENV_PATH" "SMTP env file" \
    "Create it: printf 'SN2_BACKUP_APP_PASSWORD=<16-char app password>\\n' > $ENV_PATH && chmod 600 $ENV_PATH" \
    && CRED_OK=$((CRED_OK + 1))

if [[ -e "$ENV_PATH" ]] && ! grep -q '^SN2_BACKUP_APP_PASSWORD=' "$ENV_PATH"; then
    err "$ENV_PATH exists but does not define SN2_BACKUP_APP_PASSWORD"
    CRED_OK=0
fi

if [[ $CHECK_ONLY -eq 1 ]]; then
    if [[ $CRED_OK -ne 3 ]]; then
        err "checks did not pass (see above)"
        exit 1
    fi
    info "all credential files present"
    exit 0
fi

if [[ $CRED_OK -ne 3 ]]; then
    err "Resolve the missing items above and rerun."
    exit 1
fi

# ---------- install dir + venv ----------
step "Install directory & venv"
mkdir -p "$SN2_DIR"
chmod 700 "$SN2_DIR"

# If a previous run created a partial venv (e.g. before python3-venv was
# installed), bin/python may exist but bin/pip won't. Treat that as broken.
if [[ -d "$VENV_DIR" ]] && [[ ! -x "$VENV_DIR/bin/pip" ]]; then
    warn "venv at $VENV_DIR is incomplete (no pip); recreating"
    rm -rf "$VENV_DIR"
fi

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    info "creating venv"
    python3 -m venv "$VENV_DIR"
else
    info "venv already exists"
fi

info "upgrading pip"
"$VENV_DIR/bin/pip" install --quiet --upgrade pip

info "installing sn2_backup (editable) + dependencies"
"$VENV_DIR/bin/pip" install --quiet -e "$REPO_ROOT"

# ---------- config ----------
step "Config file"
if [[ ! -e "$CONFIG_PATH" ]]; then
    cp "$REPO_ROOT/config.example.yaml" "$CONFIG_PATH"
    chmod 600 "$CONFIG_PATH"
    info "wrote $CONFIG_PATH from template"
    warn "if you want to pin a specific robot_id, edit $CONFIG_PATH"
else
    info "$CONFIG_PATH already exists (left untouched)"
fi

# ---------- dry-run smoke test ----------
step "Dry-run smoke test"
if "$VENV_DIR/bin/python" -m sn2_backup --config "$CONFIG_PATH" --dry-run -v; then
    info "dry-run finished without error"
else
    warn "dry-run reported non-zero. Inspect output above before enabling the timer."
fi

# ---------- systemd ----------
if [[ $NO_SYSTEMD -eq 1 ]]; then
    step "Skipping systemd install (--no-systemd)"
else
    step "Installing systemd units (sudo required)"
    sudo install -m 0644 "$REPO_ROOT/systemd/sn2-backup.service" "$SYSTEMD_DIR/sn2-backup.service"
    sudo install -m 0644 "$REPO_ROOT/systemd/sn2-backup.timer"   "$SYSTEMD_DIR/sn2-backup.timer"
    sudo systemctl daemon-reload
    sudo systemctl enable --now sn2-backup.timer
    info "timer enabled and started"

    step "Status"
    systemctl --no-pager status sn2-backup.timer || true
    echo
    info "next scheduled fire:"
    systemctl list-timers --no-pager sn2-backup.timer || true
fi

step "Done"
cat <<EOF

Useful commands:

  Tail logs:                 journalctl -u sn2-backup -f
  Run one cycle now:         sudo systemctl start sn2-backup.service
  See timer schedule:        systemctl list-timers sn2-backup.timer
  Re-validate setup:         bash scripts/install_on_robot.sh --check
  Disable:                   sudo systemctl disable --now sn2-backup.timer

Drive uploads land under your parent folder, sub-foldered by robot_id.
The first cycle will email you the resolved robot_id (if announce_once=true).
EOF
