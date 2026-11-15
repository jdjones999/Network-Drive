#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 jdjones999 — https://github.com/jdjones999/Network-Drive
# ──────────────────────────────────────────────────────────────────────────────
#  Network Drive installer
#
#  Install straight from GitHub:
#      curl -fsSL https://raw.githubusercontent.com/jdjones999/Network-Drive/main/install.sh | sudo bash
#
#  Or from a clone of the repo:
#      git clone https://github.com/jdjones999/Network-Drive.git
#      cd Network-Drive
#      sudo bash install.sh
#
#  What it does:
#    1. Installs the packages the tool needs (python3, figlet, samba, ntfs-3g, …)
#    2. Installs the "rowancap" figlet font used for the title
#    3. Installs the tool as /usr/local/bin/samba-setup (owner root, mode 755)
#    4. Makes sure Samba is enabled, running, and allows guest shares
#    5. Checks everything and prints a summary
#
#  Safe to run again at any time: it updates the tool and skips what's done.
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Where everything comes from ──────────────────────────────────────────────
REPO_RAW="https://raw.githubusercontent.com/jdjones999/Network-Drive/main"

APP_NAME="samba-setup"                          # the command you run
SCRIPT_FILE="samba-setup.py"                    # the tool's file in the repo
INSTALL_PATH="/usr/local/bin/${APP_NAME}"

FONT_NAME="rowancap"
FONT_FILE="${FONT_NAME}.flf"
# Font sources, tried in order. Your repo first; public copies as backup.
FONT_URLS=(
  "${REPO_RAW}/${FONT_FILE}"
  "http://www.figlet.org/fonts/rowancap.flf"
  "https://raw.githubusercontent.com/pwaller/pyfiglet/main/pyfiglet/fonts-contrib/rowancap.flf"
)

# Packages the tool needs.
APT_PACKAGES=(python3 figlet samba ntfs-3g util-linux iproute2 curl ca-certificates)

# ── Pretty output ────────────────────────────────────────────────────────────
if [ -t 1 ]; then
  O=$'\033[38;5;208m'; G=$'\033[92m'; R=$'\033[91m'; Y=$'\033[93m'; C=$'\033[96m'
  D=$'\033[90m'; B=$'\033[1m'; N=$'\033[0m'
else
  O=""; G=""; R=""; Y=""; C=""; D=""; B=""; N=""
fi
step() { printf '\n  %s▌%s%s\n' "$O$B" "$1" "$N"; }
ok()   { printf '  %s✔%s %s\n' "$G" "$N" "$1"; }
info() { printf '  %s•%s %s\n' "$C" "$N" "$1"; }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$1"; }
fail() { printf '  %s✖%s %s\n' "$R" "$N" "$1"; }
die()  { fail "$1"; exit 1; }

# Download a URL to a file. Returns non-zero on any failure (404, no network, …).
download() { curl -fsSL --retry 2 --connect-timeout 10 "$1" -o "$2" 2>/dev/null; }

# True if the file is a real figlet font (they all start with "flf2a").
is_font() { [ -s "$1" ] && head -c 5 "$1" | grep -q '^flf2a'; }

main() {
  local local_dir=""
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT

  # If run from a file (a clone), remember its folder so local copies are used.
  # When piped from curl there's no file, so everything is downloaded instead.
  if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
    local_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  fi

  printf '\n  %s◆  Network Drive installer%s\n' "$O$B" "$N"

  # ── 0. Requirements ────────────────────────────────────────────────────────
  if [ "$(id -u)" -ne 0 ]; then
    if [ -n "$local_dir" ]; then
      info "Needs root. Re-running with sudo…"
      exec sudo bash "${BASH_SOURCE[0]}" "$@"
    fi
    die "Please run with sudo:  curl -fsSL ${REPO_RAW}/install.sh | sudo bash"
  fi
  command -v apt-get   >/dev/null 2>&1 || die "This installer supports Debian/Ubuntu (apt) only."
  command -v systemctl >/dev/null 2>&1 || die "systemd is required."

  # ── 1. Packages ────────────────────────────────────────────────────────────
  step "Packages"
  local p missing=()
  for p in "${APT_PACKAGES[@]}"; do
    if dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "install ok installed"; then
      ok "$p ${D}(already installed)${N}"
    else
      missing+=("$p")
    fi
  done
  if [ "${#missing[@]}" -gt 0 ]; then
    info "Installing: ${missing[*]}"
    export DEBIAN_FRONTEND=noninteractive
    # </dev/null so apt can't swallow the rest of a piped script
    apt-get update -qq </dev/null || die "apt-get update failed. Check your internet connection."
    apt-get install -y -qq "${missing[@]}" </dev/null >/dev/null || die "Could not install: ${missing[*]}"
    for p in "${missing[@]}"; do ok "$p ${D}(installed)${N}"; done
  fi

  # ── 2. Title font ──────────────────────────────────────────────────────────
  step "Title font (${FONT_NAME})"
  local font_dir src="" url
  font_dir="$(figlet -I2 2>/dev/null || echo /usr/share/figlet)"   # figlet's font folder
  mkdir -p "$font_dir"

  if figlet -f "$FONT_NAME" test >/dev/null 2>&1; then
    ok "${FONT_NAME} already installed ${D}(${font_dir})${N}"
  else
    if [ -n "$local_dir" ] && is_font "$local_dir/$FONT_FILE"; then
      cp "$local_dir/$FONT_FILE" "$TMP/$FONT_FILE"; src="local copy"
    else
      for url in "${FONT_URLS[@]}"; do
        if download "$url" "$TMP/$FONT_FILE" && is_font "$TMP/$FONT_FILE"; then
          src="$url"; break
        fi
      done
    fi
    if [ -n "$src" ]; then
      install -m 644 -o root -g root "$TMP/$FONT_FILE" "$font_dir/$FONT_FILE"
      if figlet -f "$FONT_NAME" test >/dev/null 2>&1; then
        ok "Installed ${FONT_NAME} ${D}(from ${src})${N}"
      else
        warn "Font installed but figlet can't read it. The tool will use its built-in title."
      fi
    else
      warn "Couldn't get ${FONT_NAME}. The tool still works and uses its built-in title."
    fi
  fi

  # ── 3. The tool ────────────────────────────────────────────────────────────
  step "Installing ${APP_NAME}"
  if [ -n "$local_dir" ] && [ -f "$local_dir/$SCRIPT_FILE" ]; then
    cp "$local_dir/$SCRIPT_FILE" "$TMP/$SCRIPT_FILE"; src="local copy"
  else
    download "${REPO_RAW}/${SCRIPT_FILE}" "$TMP/$SCRIPT_FILE" \
      || die "Couldn't download ${REPO_RAW}/${SCRIPT_FILE}"
    src="GitHub"
  fi

  sed -i 's/\r$//' "$TMP/$SCRIPT_FILE"            # strip Windows line endings, just in case
  python3 -m py_compile "$TMP/$SCRIPT_FILE" 2>/dev/null \
    || die "${SCRIPT_FILE} has errors, so nothing was changed."

  if [ -f "$INSTALL_PATH" ] && cmp -s "$TMP/$SCRIPT_FILE" "$INSTALL_PATH"; then
    ok "Already up to date ${D}(${INSTALL_PATH})${N}"
  else
    if [ -f "$INSTALL_PATH" ]; then
      cp "$INSTALL_PATH" "${INSTALL_PATH}.bak"
      info "Previous version saved as ${INSTALL_PATH}.bak"
    fi
    # root-owned, rwxr-xr-x: anyone can run it, only root can change it
    install -m 755 -o root -g root "$TMP/$SCRIPT_FILE" "$INSTALL_PATH"
    ok "Installed ${INSTALL_PATH} ${D}(from ${src}, root:root, 755)${N}"
  fi

  # ── 4. Samba ───────────────────────────────────────────────────────────────
  step "Samba"
  local smb_conf="/etc/samba/smb.conf"
  [ -f "$smb_conf" ] || die "${smb_conf} is missing even though Samba is installed."

  # Guest (no-password) shares need "map to guest = bad user" in [global].
  if grep -qiE '^[[:space:]]*map[[:space:]]+to[[:space:]]+guest' "$smb_conf"; then
    ok "Guest access already enabled"
  else
    cp "$smb_conf" "${smb_conf}.bak-$(date +%Y%m%d-%H%M%S)"
    sed -i '/^\[global\]/a\   map to guest = bad user' "$smb_conf"
    ok "Enabled guest access ${D}(map to guest = bad user)${N}"
  fi

  if testparm -s "$smb_conf" >/dev/null 2>&1; then
    ok "Samba config is valid"
  else
    warn "Samba reports a config problem. Check it with: testparm"
  fi

  if systemctl enable --now smbd >/dev/null 2>&1; then ok "smbd enabled and running"; else fail "Couldn't start smbd"; fi
  # nmbd helps Windows find the server by name; not every system has it.
  if systemctl cat nmbd.service >/dev/null 2>&1; then
    if systemctl enable --now nmbd >/dev/null 2>&1; then ok "nmbd enabled and running"; else warn "nmbd didn't start (optional)"; fi
  fi
  # Open the firewall for Samba if ufw is turned on.
  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    if ufw allow samba >/dev/null 2>&1; then ok "Firewall (ufw) allows Samba"; else warn "Couldn't add the ufw rule"; fi
  fi

  # ── 5. Final check ─────────────────────────────────────────────────────────
  step "Check"
  local problems=0
  chk() { if eval "$2" >/dev/null 2>&1; then ok "$1"; else fail "$1"; problems=$((problems + 1)); fi; }
  chk "python3"                 "command -v python3"
  chk "figlet"                  "command -v figlet"
  chk "${FONT_NAME} font"       "figlet -f $FONT_NAME test"
  chk "lsblk / blkid"           "command -v lsblk && command -v blkid"
  chk "NTFS support (ntfs-3g)"  "command -v ntfs-3g"
  chk "Samba running"           "systemctl is-active --quiet smbd"
  chk "${APP_NAME} installed"   "[ -x $INSTALL_PATH ]"
  chk "${APP_NAME} is valid"    "python3 -m py_compile $INSTALL_PATH"

  echo
  if [ "$problems" -eq 0 ]; then
    printf '  %s✔ All set.%s Run it with:  %ssudo %s%s\n\n' "$G$B" "$N" "$B" "$APP_NAME" "$N"
  else
    printf '  %s%d item(s) need attention%s (see ✖ above). The font is optional; the rest is required.\n\n' \
      "$Y$B" "$problems" "$N"
  fi
}

# Everything runs from main(), so the whole file is downloaded before anything
# executes. A cut-off download can't run half a script.
main "$@"
