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
#  Works on: Ubuntu/Debian (apt), Fedora/RHEL (dnf), Arch (pacman), openSUSE (zypper)
#
#  What it does:
#    1. Installs the packages the tool needs (python3, figlet, samba, ntfs-3g, …)
#    2. Downloads the "rowancap" figlet font and puts it in figlet's font folder
#    3. Installs the tool as /usr/local/bin/samba-setup (owner root, mode 755)
#    4. Sets up Samba (enabled, running, and only reachable from local networks/Tailscale)
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
# Font downloads, tried in order: the official FIGlet font archive first,
# then backups (a copy in this repo, if present, and pyfiglet's copy).
FONT_URLS=(
  "http://www.figlet.org/fonts/${FONT_FILE}"
  "${REPO_RAW}/${FONT_FILE}"
  "https://raw.githubusercontent.com/pwaller/pyfiglet/main/pyfiglet/fonts-contrib/${FONT_FILE}"
)
# SHA-256 fingerprint of the genuine rowancap.flf. A download is only installed if
# it matches, so a tampered file (e.g. swapped on the way over plain http) is rejected.
# Get it once on a machine where the font already works:  sha256sum "$(figlet -I2)/rowancap.flf"
# Leave empty to skip the check (not recommended).
FONT_SHA256=""

# Only these networks may connect to Samba: this machine, private home/office
# ranges, and Tailscale. Everything else (the internet) is refused.
SAMBA_HOSTS_ALLOW="127.0.0.1 ::1 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10 fc00::/7 fe80::/10"

# Where figlet keeps fonts on different systems (used only if figlet can't tell us).
FONT_DIR_GUESSES=(
  /usr/share/figlet            # Debian, Ubuntu, Fedora, Arch, openSUSE
  /usr/share/figlet/fonts
  /usr/local/share/figlet      # source installs, BSD
  /usr/share/games/figlet
)

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

# Download a URL to a file with curl (or wget). Non-zero on any failure.
download() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL --retry 2 --connect-timeout 10 "$1" -o "$2" 2>/dev/null
  elif command -v wget >/dev/null 2>&1; then
    wget -q --tries=2 --timeout=10 -O "$2" "$1" 2>/dev/null
  else
    return 1
  fi
}

# True if the file is a real figlet font (they all start with "flf2a") and,
# when FONT_SHA256 is set, matches that fingerprint exactly.
is_font() {
  [ -s "$1" ] && head -c 5 "$1" | grep -q '^flf2a' || return 1
  if [ -n "$FONT_SHA256" ]; then
    [ "$(sha256sum "$1" | cut -d' ' -f1)" = "$FONT_SHA256" ] || return 1
  fi
}

# ── Package manager support ──────────────────────────────────────────────────
# Sets PM and PACKAGES for this system.
detect_package_manager() {
  if   command -v apt-get >/dev/null 2>&1; then
    PM=apt
    PACKAGES=(python3 figlet samba ntfs-3g util-linux iproute2 curl ca-certificates)
  elif command -v dnf >/dev/null 2>&1; then
    PM=dnf
    PACKAGES=(python3 figlet samba samba-common-tools ntfs-3g util-linux iproute curl ca-certificates)
  elif command -v pacman >/dev/null 2>&1; then
    PM=pacman
    PACKAGES=(python figlet samba ntfs-3g util-linux iproute2 curl ca-certificates)
  elif command -v zypper >/dev/null 2>&1; then
    PM=zypper
    PACKAGES=(python3 figlet samba ntfs-3g util-linux iproute2 curl ca-certificates)
  else
    die "Unsupported system: needs apt, dnf, pacman or zypper."
  fi
}

is_installed() {   # is_installed PACKAGE
  case "$PM" in
    apt)        dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q "install ok installed" ;;
    dnf|zypper) rpm -q "$1" >/dev/null 2>&1 ;;
    pacman)     pacman -Q "$1" >/dev/null 2>&1 ;;
  esac
}

install_packages() {   # install_packages PKG...  (</dev/null: can't eat a piped script)
  case "$PM" in
    apt)    export DEBIAN_FRONTEND=noninteractive
            apt-get update -qq </dev/null && apt-get install -y -qq "$@" </dev/null >/dev/null ;;
    dnf)    dnf install -y -q "$@" </dev/null >/dev/null ;;
    pacman) pacman -Sy --needed --noconfirm "$@" </dev/null >/dev/null ;;
    zypper) zypper --non-interactive --quiet install "$@" </dev/null >/dev/null ;;
  esac
}

# figlet's font folder on this system.
figlet_font_dir() {
  local d
  d="$(figlet -I2 2>/dev/null || true)"            # figlet reports its own folder
  if [ -n "$d" ] && [ -d "$d" ]; then echo "$d"; return; fi
  for d in "${FONT_DIR_GUESSES[@]}"; do             # otherwise find standard.flf
    if [ -f "$d/standard.flf" ]; then echo "$d"; return; fi
  done
  echo /usr/share/figlet
}

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
  command -v systemctl >/dev/null 2>&1 || die "systemd is required."
  detect_package_manager
  info "Package manager: ${PM}"

  # ── 1. Packages ────────────────────────────────────────────────────────────
  step "Packages"
  local p missing=()
  for p in "${PACKAGES[@]}"; do
    if is_installed "$p"; then ok "$p ${D}(already installed)${N}"; else missing+=("$p"); fi
  done
  if [ "${#missing[@]}" -gt 0 ]; then
    info "Installing: ${missing[*]}"
    if ! install_packages "${missing[@]}"; then
      [ "$PM" = dnf ] && info "On RHEL/Rocky/Alma, figlet and ntfs-3g need EPEL: sudo dnf install epel-release"
      die "Could not install: ${missing[*]}"
    fi
    for p in "${missing[@]}"; do ok "$p ${D}(installed)${N}"; done
  fi

  # ── 2. Title font ──────────────────────────────────────────────────────────
  step "Title font (${FONT_NAME})"
  local font_dir src="" url
  font_dir="$(figlet_font_dir)"
  mkdir -p "$font_dir"
  info "figlet font folder: ${font_dir}"

  if figlet -f "$FONT_NAME" test >/dev/null 2>&1; then
    ok "${FONT_NAME} already installed"
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
      [ -z "$FONT_SHA256" ] && warn "Font fingerprint not set in install.sh, so the download wasn't verified."
      # Fonts are plain data: owned by root, readable by everyone (644).
      install -m 644 -o root -g root "$TMP/$FONT_FILE" "$font_dir/$FONT_FILE"
      if figlet -f "$FONT_NAME" test >/dev/null 2>&1; then
        ok "Installed ${font_dir}/${FONT_FILE} ${D}(from ${src})${N}"
      else
        warn "Font installed but figlet can't read it. The tool will use its built-in title."
      fi
    else
      warn "Couldn't get a verified copy of ${FONT_NAME}. The tool still works and uses its built-in title."
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

  # A symlink here would make "sudo samba-setup" run a file that may be editable
  # without root (e.g. one in a home folder). Replace it with a real root-owned copy.
  if [ -L "$INSTALL_PATH" ]; then
    warn "${INSTALL_PATH} was a link to $(readlink "$INSTALL_PATH"). Replacing it with a root-owned copy."
    rm -f "$INSTALL_PATH"
  fi

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
  local smb_conf="/etc/samba/smb.conf" smb_svc nmb_svc
  mkdir -p /etc/samba
  if [ ! -f "$smb_conf" ]; then
    # Some systems (e.g. Arch) ship no config at all: create a minimal one.
    printf '[global]\n   workgroup = WORKGROUP\n   server string = Samba Server\n' > "$smb_conf"
    chmod 644 "$smb_conf"
    ok "Created ${smb_conf}"
  fi

  # Shares are password-protected by default (guest access is opt-in inside the tool).
  # Also refuse connections from outside private networks, so a router mistake
  # (like port-forwarding 445) can't expose the drives to the internet.
  if grep -qiE '^[[:space:]]*hosts[[:space:]]+allow' "$smb_conf"; then
    ok "Network restriction already set ${D}(hosts allow)${N}"
  else
    local allow="$SAMBA_HOSTS_ALLOW" pfx
    # Home networks often use public IPv6 addresses; allow this server's own IPv6 networks too.
    for pfx in $(ip -6 route show 2>/dev/null | awk '$1 ~ /\/[0-9]+$/ && $1 !~ /^fe80/ && $1 !~ /^ff/ {print $1}' | sort -u); do
      allow="$allow $pfx"
    done
    cp "$smb_conf" "${smb_conf}.bak-$(date +%Y%m%d-%H%M%S)"
    if grep -q '^\[global\]' "$smb_conf"; then
      sed -i "/^\[global\]/a\\   # Added by Network Drive: only local networks and Tailscale may connect\n   hosts allow = ${allow}\n   hosts deny = ALL" "$smb_conf"
    else
      sed -i "1i [global]\n   hosts allow = ${allow}\n   hosts deny = ALL\n" "$smb_conf"
    fi
    ok "Samba only accepts local networks and Tailscale ${D}(hosts allow)${N}"
  fi

  if testparm -s "$smb_conf" >/dev/null 2>&1; then
    ok "Samba config is valid"
  else
    warn "Samba reports a config problem. Check it with: testparm"
  fi

  # Service names differ: smbd/nmbd (Debian, Ubuntu) vs smb/nmb (Fedora, Arch, openSUSE).
  if systemctl cat smbd.service >/dev/null 2>&1; then smb_svc=smbd; nmb_svc=nmbd; else smb_svc=smb; nmb_svc=nmb; fi
  if systemctl enable --now "$smb_svc" >/dev/null 2>&1; then ok "${smb_svc} enabled and running"; else fail "Couldn't start ${smb_svc}"; fi
  # nmb(d) helps Windows find the server by name; optional.
  if systemctl cat "${nmb_svc}.service" >/dev/null 2>&1; then
    if systemctl enable --now "$nmb_svc" >/dev/null 2>&1; then ok "${nmb_svc} enabled and running"; else warn "${nmb_svc} didn't start (optional)"; fi
  fi

  # Open the firewall for Samba (ufw on Ubuntu, firewalld on Fedora/openSUSE).
  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    if ufw allow samba >/dev/null 2>&1; then ok "Firewall (ufw) allows Samba"; else warn "Couldn't add the ufw rule"; fi
  elif command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
    if firewall-cmd --permanent --add-service=samba >/dev/null 2>&1 && firewall-cmd --reload >/dev/null 2>&1; then
      ok "Firewall (firewalld) allows Samba"
    else
      warn "Couldn't add the firewalld rule"
    fi
  fi

  # SELinux (Fedora/RHEL) blocks Samba from sharing folders unless allowed.
  if command -v getenforce >/dev/null 2>&1 && [ "$(getenforce 2>/dev/null)" = "Enforcing" ]; then
    if setsebool -P samba_export_all_rw on >/dev/null 2>&1; then ok "SELinux allows Samba shares"; else warn "Couldn't set SELinux samba_export_all_rw"; fi
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
  chk "Samba running"           "systemctl is-active --quiet $smb_svc"
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
