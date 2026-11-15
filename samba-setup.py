#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 jdjones999 — https://github.com/jdjones999/Network-Drive
"""
Network Drive — Drive & Share Manager

Mounts drives automatically at boot (via /etc/fstab) and shares them on the
network with Samba, from a simple menu in the terminal.

Install:  curl -fsSL https://raw.githubusercontent.com/jdjones999/Network-Drive/main/install.sh | sudo bash
Run:      sudo samba-setup

Settings you can change are near the top of each section, marked in CAPITALS:
  TITLE_TEXT / TITLE_FONT / TITLE_COLOR   the big title at the top
  STARTUP_BLANK_LINES / STARTUP_DELAY     how the screen is cleared on start
  WARN_AT_PERCENT                         when ⚠ appears next to a drive
  BAR_STYLE / TERMINAL_BG                 how the storage bars look
"""
import os
import re
import shutil
import socket
import subprocess
import sys
from datetime import datetime

# ───────────────────────────── styling ─────────────────────────────

USE_COLOR = (sys.stdout.isatty() or os.environ.get("FORCE_COLOR")) and not os.environ.get("NO_COLOR")


def _c(code):
    return f"\033[{code}m" if USE_COLOR else ""


RESET, BOLD, DIM = _c("0"), _c("1"), _c("2")
RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN, GRAY = (
    _c("91"), _c("92"), _c("93"), _c("94"), _c("95"), _c("96"), _c("90"))
ORANGE = _c("38;5;208")

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def vlen(s):
    """Visible length of a string (ignores color codes)."""
    return len(_ANSI.sub("", s))


def pad(s, width, align="<"):
    gap = max(0, width - vlen(s))
    return s + " " * gap if align == "<" else " " * gap + s


def trunc(s, n):
    return s if len(s) <= n else s[: n - 1] + "…"


CARD_W = 0  # shared inner width so every card lines up (set by fit_cards)


def box(lines, title="", color=CYAN, min_width=0):
    """Return a rounded box around the given lines."""
    title_txt = f" {BOLD}{title}{RESET}{color} " if title else ""
    inner = max([vlen(l) for l in lines] + [vlen(title_txt) + 2, min_width, CARD_W])
    out = [f"{color}╭─{title_txt}{'─' * (inner + 1 - vlen(title_txt))}╮{RESET}"]
    for l in lines:
        if _ANSI.sub("", l) and set(_ANSI.sub("", l)) == {"─"}:  # divider: stretch to full width
            l = f"{GRAY}{'─' * inner}{RESET}"
        out.append(f"{color}│{RESET} {pad(l, inner)} {color}│{RESET}")
    out.append(f"{color}╰{'─' * (inner + 2)}╯{RESET}")
    return "\n".join("  " + l for l in out)


def table(headers, rows, aligns=None):
    """Return table lines (header, rule, rows) with aligned columns."""
    aligns = aligns or ["<"] * len(headers)
    widths = [max([vlen(h)] + [vlen(r[i]) for r in rows]) for i, h in enumerate(headers)]
    gap = "  "

    def fmt(cells):
        return gap.join(pad(c, widths[i], aligns[i]) for i, c in enumerate(cells)).rstrip()

    total = sum(widths) + len(gap) * (len(widths) - 1)
    return [f"{BOLD}{fmt(headers)}{RESET}", f"{GRAY}{'─' * total}{RESET}"] + [fmt(r) for r in rows]


def ok(msg):    print(f"  {GREEN}✔{RESET} {msg}")
def fail(msg):  print(f"  {RED}✖{RESET} {msg}")
def warn(msg):  print(f"  {YELLOW}!{RESET} {msg}")
def info(msg):  print(f"  {BLUE}•{RESET} {msg}")


def section(title):
    print(f"\n  {ORANGE}{BOLD}▌{title}{RESET}\n")


def ask(prompt, default=None):
    hint = f" {GRAY}[{default}]{RESET}" if default else ""
    ans = input(f"\n  {MAGENTA}?{RESET} {BOLD}{prompt}{RESET}{hint} {CYAN}›{RESET} ").strip()
    return ans or (default or "")


def confirm(prompt, default=True):
    choice = f"{GRAY}({'Y/n' if default else 'y/N'}){RESET}"
    ans = input(f"\n  {MAGENTA}?{RESET} {BOLD}{prompt}{RESET} {choice} {CYAN}›{RESET} ").strip().lower()
    return default if not ans else ans in ("y", "yes")


def pick_numbers(prompt, maximum):
    """Ask for a comma-separated list of numbers. Returns [] if cancelled."""
    while True:
        ans = ask(f"{prompt} {GRAY}(e.g. 1,3 · Enter to cancel){RESET}")
        if ans.lower() in ("", "q", "quit"):
            return []
        try:
            nums = sorted({int(x) for x in ans.replace(" ", "").split(",") if x})
        except ValueError:
            fail("Please enter numbers separated by commas.")
            continue
        if nums and all(1 <= n <= maximum for n in nums):
            return nums
        fail(f"Choose numbers between 1 and {maximum}.")


def human(num_bytes):
    try:
        b = int(num_bytes)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1000 or unit == "TB":
            return f"{b:.0f} {unit}" if unit == "B" else f"{b:.2f} {unit}"
        b /= 1024


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


# Internal-only interfaces other machines can't connect to (Docker, VMs).
SKIP_IFACES = ("lo", "docker", "br-", "veth", "virbr", "cni", "flannel")


def get_ips():
    """Reachable IPv4 addresses as [(ip, kind, interface)], LAN first, Tailscale last."""
    ips = []
    try:
        out = run(["ip", "-4", "-o", "addr", "show", "scope", "global"]).stdout
    except OSError:
        out = ""
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        iface, ip = parts[1], parts[3].split("/")[0]
        if iface.startswith(SKIP_IFACES):
            continue
        if iface.startswith("tailscale"):
            kind = "Tailscale"
        elif iface.startswith("wl"):
            kind = "Wi-Fi"
        else:
            kind = "Ethernet"
        ips.append((ip, kind, iface))
    # The interface the server actually routes through (lowest metric) goes first.
    try:
        m = re.search(r"\bdev (\S+)", run(["ip", "route", "get", "1.1.1.1"]).stdout)
        main_iface = m.group(1) if m else None
    except OSError:
        main_iface = None
    order = {"Ethernet": 0, "Wi-Fi": 1, "Tailscale": 2}
    ips.sort(key=lambda x: (x[1] == "Tailscale", x[2] != main_iface, order[x[1]]))
    return ips


def primary_ip():
    ips = get_ips()
    return ips[0][0] if ips else "unknown-ip"


def ip_lines(ips, share=None):
    """One aligned line per address. With share, shows \\\\ip\\share paths."""
    if not ips:
        return [f"{GRAY}No network addresses found{RESET}"]
    addrs = [f"\\\\{ip}\\{share}" if share else ip for ip, _, _ in ips]
    w = max(len(a) for a in addrs)
    kw = max(len(kind) for _, kind, _ in ips)
    # Three even columns: address │ connection type │ interface name
    return [f"{GREEN}●{RESET} {BOLD}{a.ljust(w)}{RESET}    {GRAY}{kind.ljust(kw)}    {iface}{RESET}"
            for a, (_, kind, iface) in zip(addrs, ips)]


# ───────────────────────────── system info ─────────────────────────────

FSTAB = "/etc/fstab"
SMB_CONF = "/etc/samba/smb.conf"

# Partitions that hold LVM, swap, RAID or encryption and must not be mounted directly.
NOT_MOUNTABLE = {"LVM2_member", "swap", "crypto_LUKS", "linux_raid_member", "zfs_member", "BitLocker"}
# Mount points that should never be offered as network shares.
SYSTEM_MOUNTS = {"/", "/boot", "/boot/efi", "/home", "/var", "/usr", "/tmp", "none", "swap"}


def get_drives():
    """All partitions with a filesystem, from lsblk + blkid."""
    drives = []
    try:
        out = run(["lsblk", "-r", "-b", "-pno", "NAME,TYPE,SIZE,MOUNTPOINT"]).stdout
    except OSError as e:
        fail(f"Could not list drives: {e}")
        return []

    for line in out.strip().splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[1] != "part" or "/dev/loop" in parts[0]:
            continue
        name, size = parts[0], parts[2]
        mountpoint = parts[3].replace("\\x20", " ") if len(parts) > 3 else ""

        info_ = {}
        for bline in run(["blkid", name, "-o", "export"]).stdout.splitlines():
            if "=" in bline:
                k, v = bline.split("=", 1)
                info_[k] = v.strip('"')
        if info_.get("UUID") and info_.get("TYPE"):
            drives.append({
                "NAME": name.split("/")[-1],
                "SIZE": size,
                "FSTYPE": info_["TYPE"],
                "UUID": info_["UUID"],
                "LABEL": info_.get("LABEL"),
                "MOUNTPOINT": mountpoint,
            })
    return drives


def read_fstab():
    """Returns ({uuid: mount_point}, {all mount points in fstab})."""
    by_uuid, targets = {}, set()
    try:
        with open(FSTAB) as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                parts = s.split()
                if len(parts) < 2:
                    continue
                targets.add(parts[1])
                # Handles UUID=..., UUID="..." and /dev/disk/by-uuid/... (any case, e.g. NTFS IDs)
                m = re.match(r'(?:UUID="?|/dev/disk/by-uuid/)([^"\s]+)', parts[0])
                if m:
                    by_uuid[m.group(1)] = parts[1]
    except OSError as e:
        warn(f"Could not read {FSTAB}: {e}")
    return by_uuid, targets


def read_samba_shares():
    """Returns ({path: share_name}, {all section names, lower-case})."""
    shares, sections, current = {}, set(), None
    try:
        with open(SMB_CONF) as f:
            for line in f:
                s = line.strip()
                if not s or s[0] in "#;":
                    continue
                m = re.match(r"\[(.+)\]", s)
                if m:
                    current = m.group(1)
                    sections.add(current.lower())
                elif "=" in s and current:
                    k, v = s.split("=", 1)
                    if k.strip().lower() == "path":
                        shares[v.strip().rstrip("/")] = current
    except FileNotFoundError:
        pass
    except OSError as e:
        warn(f"Could not read {SMB_CONF}: {e}")
    return shares, sections


def read_guest_shares():
    """Names of shares anyone can open without a password (guest ok / public = yes)."""
    guests, current = set(), None
    try:
        with open(SMB_CONF) as f:
            for line in f:
                s = line.strip()
                if not s or s[0] in "#;":
                    continue
                m = re.match(r"\[(.+)\]", s)
                if m:
                    current = m.group(1)
                elif "=" in s and current:
                    k, v = (x.strip().lower() for x in s.split("=", 1))
                    if k in ("guest ok", "public") and v in ("yes", "true", "1"):
                        guests.add(current)
    except OSError:
        pass
    return guests


def share_status(mp, shares, guests):
    """Short colored label for the Network column."""
    name = shares.get(mp.rstrip("/"))
    if name is None:
        return f"{GRAY}○ local{RESET}"
    if name in guests:
        return f"{YELLOW}● guest{RESET}"          # open to anyone on the network
    return f"{GREEN}● shared{RESET}"              # password-protected


# ───────────────────────────── views ─────────────────────────────

def drive_table(drives, fstab_uuids, fstab_targets):
    """Print the drive table and return the list of drives that can be set up."""
    lines, available = drive_table_lines(drives, fstab_uuids, fstab_targets)
    print(box(lines, title="Drives"))
    return available


def drive_table_lines(drives, fstab_uuids, fstab_targets):
    rows, available = [], []
    for d in drives:
        fs, mounted = d["FSTYPE"], d["MOUNTPOINT"]
        auto = fstab_uuids.get(d["UUID"])
        label = d["LABEL"] if d["LABEL"] else f"{GRAY}—{RESET}"
        num, dev = "", f"/dev/{d['NAME']}"

        if fs in NOT_MOUNTABLE:
            status, where = f"{GRAY}· system volume{RESET}", f"{GRAY}—{RESET}"
            dev, label = f"{GRAY}{dev}{RESET}", f"{GRAY}{_ANSI.sub('', label)}{RESET}"
        elif mounted and (auto or mounted in fstab_targets):
            status, where = f"{GREEN}● mounted · at boot{RESET}", mounted
        elif mounted:
            status, where = f"{YELLOW}● mounted · this boot only{RESET}", mounted
        elif auto:
            status, where = f"{YELLOW}○ set up · not mounted{RESET}", f"{GRAY}{auto}{RESET}"
        else:
            available.append(d)
            num = f"{CYAN}{BOLD}{len(available)}{RESET}"
            status, where = f"{CYAN}○ available{RESET}", f"{GRAY}—{RESET}"

        rows.append([num, dev, human(d["SIZE"]), label, fs, where, status])

    lines = table(["#", "Device", "Size", "Label", "Type", "Mount point", "Status"],
                  rows, [">", "<", ">", "<", "<", "<", "<"])
    return lines, available


def share_table(candidates, shares, ip):
    rows, unshared = [], []
    guests = read_guest_shares()
    for mp in candidates:
        if mp in shares:
            kind = (f"{YELLOW}● shared · guest (no password){RESET}" if shares[mp] in guests
                    else f"{GREEN}● shared · password{RESET}")
            rows.append(["", mp, f"{kind}  {GRAY}\\\\{ip}\\{shares[mp]}{RESET}"])
        else:
            unshared.append(mp)
            rows.append([f"{CYAN}{BOLD}{len(unshared)}{RESET}", mp, f"{CYAN}○ not shared{RESET}"])
    print(box(table(["#", "Mount point", "Status"], rows, [">", "<", "<"]), title="Network shares"))
    return unshared


def preview(mount_point, limit=12):
    try:
        entries = list(os.scandir(mount_point))
    except OSError as e:
        fail(f"Could not open {mount_point}: {e.strerror}")
        return

    def is_dir(e):
        try:
            return e.is_dir()
        except OSError:
            return False

    entries.sort(key=lambda e: (not is_dir(e), e.name.lower()))
    lines = []
    for e in entries[:limit]:
        if is_dir(e):
            lines.append(f"{BLUE}▸ {trunc(e.name, 60)}/{RESET}")
        else:
            try:
                size = human(e.stat().st_size)
            except OSError:
                size = "?"
            lines.append(f"  {trunc(e.name, 60)}  {GRAY}{size}{RESET}")
    if not entries:
        lines.append(f"{GRAY}(empty){RESET}")
    elif len(entries) > limit:
        lines.append(f"{GRAY}… and {len(entries) - limit} more{RESET}")
    print(box(lines, title=f"{mount_point} · {len(entries)} items", color=GRAY))


# ───────────────────────────── actions ─────────────────────────────

def samba_service():
    """Samba's service name: smbd on Debian/Ubuntu, smb on Fedora/openSUSE/Arch."""
    for name in ("smbd", "smb"):
        if run(["systemctl", "cat", f"{name}.service"]).returncode == 0:
            return name
    return "smbd"


def sanitize(name):
    return re.sub(r"[^\w-]", "", (name or "").replace(" ", "-")).strip("-")


def mount_drives():
    section("Mount drives at boot")
    drives = get_drives()
    if not drives:
        fail("No drives detected.")
        return
    fstab_uuids, fstab_targets = read_fstab()
    available = drive_table(drives, fstab_uuids, fstab_targets)
    if not available:
        print()
        ok("Every drive is already set up. Nothing to do.")
        return

    nums = pick_numbers("Which drive(s) should mount automatically?", len(available))
    if not nums:
        info("Cancelled. No changes made.")
        return

    taken = set(fstab_targets) | {d["MOUNTPOINT"] for d in drives if d["MOUNTPOINT"]}
    plan = []
    for n in nums:
        d = available[n - 1]
        default = sanitize(d["LABEL"]) or None
        while True:
            name = sanitize(ask(f"Folder name for {CYAN}/dev/{d['NAME']}{RESET}{BOLD} under /mnt", default))
            if not name:
                fail("The name can't be empty.")
                continue
            mp = f"/mnt/{name}"
            if mp in taken:
                fail(f"{mp} is already in use. Pick another name.")
                continue
            taken.add(mp)
            plan.append((d, mp))
            break

    print()
    print(box([f"/dev/{d['NAME']}  {GRAY}→{RESET}  {BOLD}{mp}{RESET}  {GRAY}({d['FSTYPE']}, {human(d['SIZE'])}){RESET}"
               for d, mp in plan], title="Ready to add", color=YELLOW))
    if not confirm("Write these to /etc/fstab?", default=False):
        info("Cancelled. No changes made.")
        return

    # Back up fstab
    backup = f"{FSTAB}.bak-{datetime.now():%Y%m%d-%H%M%S}"
    try:
        shutil.copy2(FSTAB, backup)
        ok(f"Backed up fstab to {GRAY}{backup}{RESET}")
    except OSError as e:
        fail(f"Could not back up fstab ({e}). Stopping without changes.")
        return

    # Write entries. nofail + short timeout = server still boots if a drive is missing.
    try:
        with open(FSTAB, "r+") as f:
            content = f.read()
            if content and not content.endswith("\n"):
                f.write("\n")
            f.write(f"\n# Added by samba-setup.py on {datetime.now():%Y-%m-%d %H:%M}\n")
            for d, mp in plan:
                os.makedirs(mp, exist_ok=True)
                fs_pass = 2 if d["FSTYPE"] in ("ext2", "ext3", "ext4") else 0
                opts = "defaults,nofail,nodiscard,x-systemd.device-timeout=10s"
                f.write(f"UUID={d['UUID']}\t{mp}\t{d['FSTYPE']}\t{opts}\t0\t{fs_pass}\n")
        ok("Updated /etc/fstab")
    except OSError as e:
        fail(f"Could not write fstab: {e}")
        return

    run(["systemctl", "daemon-reload"])

    for d, mp in plan:
        r = run(["mount", mp])
        if r.returncode == 0 or os.path.ismount(mp):
            ok(f"Mounted {BOLD}{mp}{RESET}")
            print()
            preview(mp)
        else:
            fail(f"Could not mount {mp}: {r.stderr.strip()}")
            info(f"Your previous fstab is saved at {backup}")


def setup_samba():
    section("Share drives on the network")
    if not os.path.exists(SMB_CONF):
        fail("Samba isn't installed. Run the Network Drive installer again to set it up.")
        return

    fstab_uuids, _ = read_fstab()
    candidates = sorted({mp for mp in fstab_uuids.values() if mp not in SYSTEM_MOUNTS})
    if not candidates:
        warn("No data drives are set up yet. Use option 1 to mount one first.")
        return

    ip = primary_ip()
    shares, sections = read_samba_shares()
    unshared = share_table(candidates, shares, ip)
    if not unshared:
        print()
        ok("Every mounted drive is already shared.")
        return

    nums = pick_numbers("Which drive(s) should be shared?", len(unshared))
    if not nums:
        info("Cancelled. No changes made.")
        return

    new = []
    for n in nums:
        mp = unshared[n - 1]
        base = name = os.path.basename(mp) or "share"
        i = 2
        while name.lower() in sections:
            name, i = f"{base}-{i}", i + 1
        sections.add(name.lower())
        new.append((name, mp))

    # ── Who may connect ──
    access = choose_share_access()
    if access is None:
        info("Cancelled. No changes made.")
        return
    user = None
    if access == "password":
        user = choose_share_user()
        if not user or not ensure_samba_user(user):
            info("Cancelled. No changes made.")
            return

    backup = f"{SMB_CONF}.bak-{datetime.now():%Y%m%d-%H%M%S}"
    try:
        shutil.copy2(SMB_CONF, backup)
        if access == "guest":
            ensure_global_option("map to guest", "bad user")
        with open(SMB_CONF, "a") as f:
            for name, mp in new:
                f.write(f"\n[{name}]\n"
                        f"   comment = Network Drive share ({name})\n"
                        f"   path = {mp}\n"
                        f"   browseable = yes\n"
                        f"   read only = no\n")
                if access == "guest":
                    f.write("   guest ok = yes\n")
                else:
                    f.write(f"   guest ok = no\n   valid users = {user}\n")
        ok(f"Updated smb.conf {GRAY}(backup: {backup}){RESET}")
    except OSError as e:
        fail(f"Could not update {SMB_CONF}: {e}")
        return

    if run(["testparm", "-s"]).returncode != 0:
        warn(f"Samba reports a config problem. Check it with: {BOLD}testparm{RESET}")

    r = run(["systemctl", "restart", samba_service()])
    if r.returncode != 0:
        fail(f"Samba didn't restart: {r.stderr.strip()}")
        return
    ok("Restarted Samba")

    print()
    ips = get_ips()
    lines = []
    for name, mp in new:
        lines += [f"{BOLD}{name}{RESET}  {GRAY}{mp}{RESET}"] + ["  " + l for l in ip_lines(ips, share=name)]
    lines += ["", f"{GRAY}Windows: paste an address into File Explorer's address bar.{RESET}"]
    if access == "guest":
        lines.append(f"{YELLOW}No password: anyone on your network can open, change and delete these files.{RESET}")
        title, color = "Shared on your network (guest)", YELLOW
    else:
        lines.append(f"{GRAY}Sign in as {RESET}{BOLD}{user}{RESET}{GRAY} with your Samba password.{RESET}")
        title, color = "Shared on your network", GREEN
    print(box(lines, title=title, color=color))


# ── Share protection helpers ──

def choose_share_access():
    """'password' (default), 'guest', or None if cancelled."""
    print()
    print(box([f"{CYAN}{BOLD}1{RESET}  Password protected   {GREEN}recommended{RESET}",
               f"   {GRAY}Only the account you choose can open the share.{RESET}",
               f"{CYAN}{BOLD}2{RESET}  Guest, no password   {YELLOW}not recommended{RESET}",
               f"   {GRAY}Any device on your network can read, change and delete everything,{RESET}",
               f"   {GRAY}including malware or ransomware on another computer.{RESET}"],
              title="Who can open these shares?"))
    while True:
        ans = ask("Choose 1 or 2", "1").lower()
        if ans in ("q", "b", "cancel"):
            return None
        if ans == "1":
            return "password"
        if ans == "2":
            if confirm("Share WITHOUT a password? Anyone on your network gets full access.", default=False):
                return "guest"
            return "password" if confirm("Use password protection instead?", default=True) else None
        fail("Pick 1 or 2.")


def choose_share_user():
    """Linux account allowed to connect. Defaults to whoever ran sudo."""
    import pwd
    default = os.environ.get("SUDO_USER") or None
    if default == "root":
        default = None
    while True:
        user = ask("Which account should be allowed to connect?", default).strip()
        if not user:
            return None
        if user == "root":
            fail("Don't share as root. Pick your normal account.")
            continue
        try:
            pwd.getpwnam(user)
            return user
        except KeyError:
            fail(f"There's no account called '{user}' on this server.")


def ensure_samba_user(user):
    """Make sure the account has a Samba password (asks for one if not)."""
    if run(["pdbedit", "-L", "-u", user]).returncode == 0:
        ok(f"{user} already has a Samba password")
        return True
    info(f"Set a network password for {BOLD}{user}{RESET}. You'll type it when connecting from another computer.")
    info(f"{GRAY}It can be different from the server login password.{RESET}")
    r = subprocess.run(["smbpasswd", "-a", user])      # interactive: asks twice
    if r.returncode == 0:
        ok(f"Samba password set for {user}")
        return True
    fail("The Samba password wasn't set.")
    return False


def ensure_global_option(key, value):
    """Add 'key = value' to [global] in smb.conf if that key isn't there yet."""
    with open(SMB_CONF) as f:
        text = f.read()
    if re.search(rf"^\s*{re.escape(key)}\s*=", text, re.I | re.M):
        return
    if re.search(r"^\[global\]", text, re.M):
        text = re.sub(r"^\[global\][^\n]*\n", lambda m: m.group(0) + f"   {key} = {value}\n", text, count=1, flags=re.M)
    else:
        text = f"[global]\n   {key} = {value}\n\n" + text
    with open(SMB_CONF, "w") as f:
        f.write(text)


# Show ⚠ next to a drive's percentage once it's this full (or fuller).
# This is the default; change it from the menu: 4 Settings (saved in /etc/network-drive/settings.conf).
WARN_AT_PERCENT = 85

# Order of drives in the Storage panel (also changeable from 4 Settings).
SORT_DRIVES = "name"
SORT_OPTIONS = {                       # setting value → menu label
    "name":     "Name (A → Z)",
    "usage":    "Fullness (least → most full)",
    "capacity": "Capacity (smallest → largest)",
}
SORT_SHORT = {"name": "name", "usage": "fullness", "capacity": "capacity"}

# Storage bar style:
#   "grid"   – thin gap between segments AND between drives (default, like abtop)
#   "lines"  – ▇  thin gap between drives only; segments touch
#   "gaps"   – ▉  thin gap between segments only; drives' bars touch
#   "blocks" – ■  separate squares with space on every side
BAR_STYLE = "grid"
# Your terminal's background color. The thin gaps are painted in this color.
# Windows Terminal's default "Campbell" theme is (12, 12, 12).
TERMINAL_BG = (12, 12, 12)

# "grid" trick: 🭾 draws only a thin strip on the right and top edges of a cell.
# Painting that strip in the terminal background color and filling the cell's
# background with the bar color leaves a block with a thin gap right and above.
GRID_CHAR = "\U0001FB7E"
_BAR_CHARS = {"lines": "▇", "gaps": "▉", "blocks": "■"}
if not USE_COLOR:
    CELL, EMPTY = "#", "·"
else:
    CELL = EMPTY = _BAR_CHARS.get(BAR_STYLE, GRID_CHAR)
BAR_PREFIX = ""
# Heat gradient (24-bit): blue → steel → khaki → yellow → orange, like abtop.
GRADIENT = [
    (0.00, (24, 128, 255)),   # blue
    (0.30, (86, 150, 215)),   # steel blue
    (0.48, (160, 172, 150)),  # grey-khaki
    (0.65, (228, 196, 92)),   # sand yellow
    (0.80, (255, 190, 30)),   # amber
    (0.92, (255, 140, 20)),   # orange
    (1.00, (255, 96, 24)),    # deep orange
]
TRACK_RGB = (30, 38, 58)      # dark navy empty part of a bar
TRACK = _c("38;2;%d;%d;%d" % TRACK_RGB)


def _grad_rgb(p):
    p = min(max(p, 0.0), 1.0)
    for (p0, c0), (p1, c1) in zip(GRADIENT, GRADIENT[1:]):
        if p <= p1:
            t = (p - p0) / (p1 - p0) if p1 > p0 else 0
            return tuple(round(c0[i] + (c1[i] - c0[i]) * t) for i in range(3))
    return GRADIENT[-1][1]


def _fill_color(p):
    """Color for a position/percentage (0..1) along the gradient."""
    return _c("38;2;%d;%d;%d" % _grad_rgb(p))


def _pct_color(p):
    return _c("38;2;%d;%d;%d" % _grad_rgb(p)) + BOLD


def usage_bar(frac, width=24):
    """Segmented gradient bar: each filled cell takes the color of its position."""
    filled = min(width, round(frac * width))
    if frac > 0 and filled == 0:
        filled = 1
    if USE_COLOR and BAR_STYLE not in _BAR_CHARS:
        # grid: cell background = bar color, glyph (thin right/top strip) = terminal background
        gap = "38;2;%d;%d;%d" % TERMINAL_BG
        cells = ["\033[%s;48;2;%d;%d;%dm%s" % ((gap,) + _grad_rgb(i / (width - 1)) + (GRID_CHAR,))
                 for i in range(filled)]
        cells += ["\033[%s;48;2;%d;%d;%dm%s" % ((gap,) + TRACK_RGB + (GRID_CHAR,))] * (width - filled)
        return "".join(cells) + RESET
    cells = [f"{_fill_color(i / (width - 1))}{CELL}" for i in range(filled)]
    cells += [f"{TRACK}{EMPTY}"] * (width - filled)
    return BAR_PREFIX + "".join(cells) + RESET


def storage_panel():
    lines = storage_lines()
    if not lines:
        return
    # Stretch the bars so the card's contents fill the shared card width.
    extra = CARD_W - max(vlen(l) for l in lines)
    if extra > 0:
        lines = storage_lines(bar_width=24 + extra)
    print()
    print(box(lines, title=f"Storage · by {SORT_SHORT[SORT_DRIVES]}", color=BLUE))


def storage_lines(bar_width=24):
    """Fill gauges for every data drive set up in fstab (None if there are none)."""
    fstab_uuids, _ = read_fstab()
    mps = {mp for mp in fstab_uuids.values() if mp.startswith("/") and mp not in SYSTEM_MOUNTS}
    if not mps:
        return None
    shares, _ = read_samba_shares()
    guests = read_guest_shares()

    # Gather usage first, so the drives can be sorted by it.
    mounted, unmounted = [], []
    for mp in mps:
        try:
            if not os.path.ismount(mp):
                raise OSError
            mounted.append((mp, shutil.disk_usage(mp)))
        except OSError:
            unmounted.append(mp)

    name_key = lambda mp: os.path.basename(mp.rstrip("/")).lower()
    if SORT_DRIVES == "usage":        # least full → most full
        mounted.sort(key=lambda d: (d[1].used / d[1].total if d[1].total else 0, name_key(d[0])))
    elif SORT_DRIVES == "capacity":   # smallest → largest
        mounted.sort(key=lambda d: (d[1].total, name_key(d[0])))
    else:                             # name, A → Z
        mounted.sort(key=lambda d: name_key(d[0]))
    unmounted.sort(key=name_key)      # drives that aren't mounted always go last

    rows = []
    for mp, u in mounted:
        name = trunc(os.path.basename(mp.rstrip("/")) or mp, 20)
        net = share_status(mp, shares, guests)
        frac = u.used / u.total if u.total else 0
        # ⚠ marks drives at or above WARN_AT_PERCENT. Some terminals (Windows Terminal)
        # draw ⚠ two columns wide, so it always gets a spare space after it to spill into.
        warn_mark = "⚠" if USE_COLOR else "!"
        mark = warn_mark if frac * 100 >= WARN_AT_PERCENT else " "
        pct = f"{_pct_color(frac)}{frac * 100:.0f}%{mark} {RESET}"
        rows.append([f"{BOLD}{name}{RESET}", usage_bar(frac, bar_width), pct,
                     f"{human(u.used)} {GRAY}/{RESET} {human(u.total)}", net])
    for mp in unmounted:
        name = trunc(os.path.basename(mp.rstrip("/")) or mp, 20)
        net = share_status(mp, shares, guests)
        rows.append([f"{GRAY}{name}{RESET}", f"{GRAY}not mounted{RESET}", "", "", net])
    return table(["Drive", "Usage", "", "Used / Size", "Network"], rows,
                 ["<", "<", ">", ">", "<"])


def show_status():
    section("Overview")
    drives = get_drives()
    fstab_uuids, fstab_targets = read_fstab()
    drive_table(drives, fstab_uuids, fstab_targets)
    candidates = sorted({mp for mp in fstab_uuids.values() if mp not in SYSTEM_MOUNTS})
    if candidates and os.path.exists(SMB_CONF):
        print()
        shares, _ = read_samba_shares()
        share_table(candidates, shares, primary_ip())


# ───────────────────────────── main ─────────────────────────────

# ── Startup ──
STARTUP_BLANK_LINES = 75   # pushes old output up so the tool starts on a clean screen (scrollback is kept)
STARTUP_DELAY = 0          # seconds to pause after that; set to 1 to match your `sleep 1`

TITLE_TEXT = "NETWORK DRIVE"
# High-resolution pixel font: 10 pixels tall. Two pixel rows are packed into each
# text row with half-blocks (▀ ▄ █), so pixels are square and curves/diagonals are smooth.
FONT = {
    "N": ["##....##", "###...##", "###...##", "####..##", "##.##.##",
          "##.##.##", "##..####", "##...###", "##...###", "##....##"],
    "E": ["#######", "#######", "##.....", "##.....", "######.",
          "######.", "##.....", "##.....", "#######", "#######"],
    "T": ["########", "########", "...##...", "...##...", "...##...",
          "...##...", "...##...", "...##...", "...##...", "...##..."],
    "W": ["##......##", "##......##", "##......##", "##......##", "##..##..##",
          "##..##..##", "##.####.##", "####..####", "###....###", "##......##"],
    "O": [".######.", "########", "##....##", "##....##", "##....##",
          "##....##", "##....##", "##....##", "########", ".######."],
    "R": ["#######.", "########", "##....##", "##....##", "########",
          "#######.", "##..##..", "##...##.", "##....##", "##....##"],
    "K": ["##....##", "##...##.", "##..##..", "##.##...", "####....",
          "####....", "##.##...", "##..##..", "##...##.", "##....##"],
    "D": ["######..", "#######.", "##...###", "##....##", "##....##",
          "##....##", "##....##", "##...###", "#######.", "######.."],
    "I": ["######", "######", "..##..", "..##..", "..##..",
          "..##..", "..##..", "..##..", "######", "######"],
    "V": ["##......##", "##......##", "##......##", "##......##", ".##....##.",
          ".##....##.", "..##..##..", "..##..##..", "...####...", "....##...."],
    " ": ["...."] * 10,
}
FONT_HEIGHT = 10        # pixels → 5 text rows
LETTER_GAP = 1          # blank pixel columns between letters
TITLE_COLORS = [214, 214, 208, 208, 202]  # amber → orange, top to bottom (one per text row)


# Smooth title: pre-rendered from a bold display font with soft (anti-aliased) edges.
# Each digit is how much of that pixel is covered (blank = empty, 9 = solid).
# Two pixel rows are packed into each text row using ▀/▄ with separate top and
# bottom colors, so the result is about twice as detailed as plain blocks.
TITLE_BITMAP = [
    "886   882 28888886 288888881 885  685  688    6996    488884    784  2883    388873    188886    487 1882   588  8888888",
    "999   994 49999999 499999993 999  999  999   799996   7999996   997  999     6999996   2999999   799 1996   999 19999999",
    "9993  994 39999998 499999993 999  999  998  29999991  69999991  996 1997     59999993  29999995  799  998   998 19999999",
    "9998  994 49999999 499999994 899  999  996  89999997  69999996  996 7991     59999999  29999999  799  999   996 19999999",
    "9999  994 49953332  3399933  599 1999  994  99923999  69913999  996 999      599349991 29941999  799  799  2993 19963333",
    "99992 994 499         898    399 399911992 1995  799  699  799  9953993      599  3994 2991 399  799  5991 4991 1992",
    "99997 994 4991        999    199149993299  3991  2992 699  699  996999       599   997 2992 2991 799  2993 799  1993",
    "999991994 49999993    998     99369995499  599    994 699  899  999995       599   998 299  599  799   995 999  19999996",
    "999995994 49999993    998     99589897699  599    994 69956998  999997       599   999 29965999  799   997 997  19999997",
    "996999994 49999993    998     99799598898  599    994 69999994  999999       599   999 29999998  799   999 995  19999997",
    "995599994 49999993    998     89999299996  599    994 6999999   9999992      599   998 29999992  799   6992992  19999996",
    "995 99994 4991        998     69999 99994  3991  2992 6999996   9998997      599   997 2999999   799   499599   1993",
    "996 99994 499         998     39999 99992  1995  799  6991999   999 899      599  3994 2993699   799   199999   1992",
    "996 69994 49953332    998     19998 9999    99923999  699 599   998 4993     599339991 299  993  799    99999   19963333",
    "996 19994 49999999    998      9996 7999    89999997  699 1993  996  998     59999999  2991 997  799    99997   19999999",
    "996  9994 39999998    998      9994 5999    29999991  699  997  996  999     59999993  2991 899  799    89994    9999999",
    "997  6994 49999999    999      9992 3999     799996   699  999  997  5994    6999996   2991 5991 799    59992   19999999",
    "884   882 28888886    686      588   884      6996    487  388  784   885    388883    188   882 587    1888     8888888",
]
TITLE_TOP_RGB = (255, 196, 0)      # amber at the top of the letters
TITLE_BOTTOM_RGB = (255, 84, 0)    # orange at the bottom


def _title_rgb(level, y, height):
    t = y / max(1, height - 1)
    base = [TITLE_TOP_RGB[i] + (TITLE_BOTTOM_RGB[i] - TITLE_TOP_RGB[i]) * t for i in range(3)]
    a = level / 9
    return tuple(round(TERMINAL_BG[i] + (base[i] - TERMINAL_BG[i]) * a) for i in range(3))


def _smooth_title_lines():
    h = len(TITLE_BITMAP) + len(TITLE_BITMAP) % 2
    w = max(len(r) for r in TITLE_BITMAP)
    bmp = [r.ljust(w) for r in TITLE_BITMAP] + [" " * w] * (h - len(TITLE_BITMAP))
    lvl = lambda ch: 0 if ch == " " else int(ch)
    lines = []
    for y in range(0, h, 2):
        out = []
        for x in range(w):
            a, b = lvl(bmp[y][x]), lvl(bmp[y + 1][x])
            if not USE_COLOR:
                out.append({(1, 1): "█", (1, 0): "▀", (0, 1): "▄"}.get((a >= 5, b >= 5), " "))
            elif a == 0 and b == 0:
                out.append("\033[0m ")
            elif b == 0:
                out.append("\033[0;38;2;%d;%d;%dm▀" % _title_rgb(a, y, h))
            elif a == 0:
                out.append("\033[0;38;2;%d;%d;%dm▄" % _title_rgb(b, y + 1, h))
            else:
                out.append("\033[0;38;2;%d;%d;%d;48;2;%d;%d;%dm▀"
                           % (_title_rgb(a, y, h) + _title_rgb(b, y + 1, h)))
        lines.append(("".join(out) + RESET).rstrip())
    return lines


TITLE_FONT = "rowancap"   # figlet font, same as your login banner
TITLE_COLOR = 208         # same color as your sed: \033[38;5;208m


def figlet_title_lines(text):
    """Title from figlet (like: figlet -f rowancap "$text"). None if figlet isn't available."""
    try:
        # -w 1000 stops figlet wrapping the title at its default 80 columns
        r = run(["figlet", "-w", "1000", "-f", TITLE_FONT, text])
    except OSError:
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    lines = [l.rstrip() for l in r.stdout.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return [_c(f"38;5;{TITLE_COLOR}") + l + RESET for l in lines]


def big_title_lines(text):
    fig = figlet_title_lines(text)
    if fig:
        return fig
    # figlet or the font is missing: fall back to the built-in title.
    if text.upper() == "NETWORK DRIVE":
        return _smooth_title_lines()
    # Any other title text falls back to the built-in pixel font.
    pix = [""] * FONT_HEIGHT
    for ch in text.upper():
        glyph = FONT.get(ch, FONT[" "])
        for y in range(FONT_HEIGHT):
            pix[y] += glyph[y] + "." * LETTER_GAP
    half = {("#", "#"): "█", ("#", "."): "▀", (".", "#"): "▄", (".", "."): " "}
    rows = []
    for y in range(0, FONT_HEIGHT, 2):
        row = "".join(half[(a, b)] for a, b in zip(pix[y], pix[y + 1])).rstrip()
        rows.append(_c(f"38;5;{TITLE_COLORS[y // 2]}") + row + RESET)
    return rows


def title_banner():
    rows = big_title_lines(TITLE_TEXT)
    width = max(vlen(r) for r in rows)
    card = CARD_W + 4
    if width > shutil.get_terminal_size((120, 24)).columns - 2:
        # Terminal too narrow for the big letters: plain title instead.
        print(f"\n  {ORANGE}{BOLD}{' '.join(TITLE_TEXT)}{RESET}")
        return
    indent = 2 + max(0, (card - width) // 2)   # centered over the cards
    print()
    for row in rows:
        print(" " * indent + row)


def startup_screen():
    # Scroll everything already on screen up into the scrollback (at least one full
    # window's worth), then move the cursor to the top-left so the tool draws from the top.
    rows = shutil.get_terminal_size((80, 24)).lines
    sys.stdout.write("\n" * max(STARTUP_BLANK_LINES, rows))
    if sys.stdout.isatty():
        sys.stdout.write("\033[H")
    sys.stdout.flush()
    if STARTUP_DELAY:
        import time
        time.sleep(STARTUP_DELAY)


def banner_lines():
    return ([f"{ORANGE}{BOLD}◆  Drive & Share Manager{RESET}", "",
             f"{BOLD}{socket.gethostname()}{RESET}"] + ["  " + l for l in ip_lines(get_ips())])


MENU_LINES = [f"{CYAN}{BOLD}1{RESET}  Mount drives at boot",
              f"{CYAN}{BOLD}2{RESET}  Share drives on the network",
              f"{CYAN}{BOLD}3{RESET}  Show drives & shares",
              f"{CYAN}{BOLD}4{RESET}  Settings",
              f"{GRAY}q  Quit{RESET}"]


# ───────────────────────────── settings ─────────────────────────────
# Saved settings live outside the program, so updating or reinstalling the
# tool never resets them. Simple "key = value" lines.
SETTINGS_FILE = "/etc/network-drive/settings.conf"


def load_settings():
    """Apply saved settings over the defaults above (missing/bad values are ignored)."""
    global WARN_AT_PERCENT, SORT_DRIVES
    try:
        with open(SETTINGS_FILE) as f:
            for line in f:
                key, sep, value = line.partition("=")
                if not sep or line.lstrip().startswith("#"):
                    continue
                key, value = key.strip(), value.strip()
                if key == "warn_at_percent" and value.isdigit() and 1 <= int(value) <= 100:
                    WARN_AT_PERCENT = int(value)
                elif key == "sort_drives" and value in SORT_OPTIONS:
                    SORT_DRIVES = value
    except OSError:
        pass


def save_settings():
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    with open(SETTINGS_FILE, "w") as f:
        f.write("# Network Drive settings. Change them from the tool's Settings menu.\n")
        f.write(f"warn_at_percent = {WARN_AT_PERCENT}\n")
        f.write(f"sort_drives = {SORT_DRIVES}\n")
    os.chmod(SETTINGS_FILE, 0o644)


def _save_and_report(message):
    try:
        save_settings()
        ok(f"{message} {GRAY}(saved to {SETTINGS_FILE}){RESET}")
    except OSError as e:
        fail(f"Couldn't save settings: {e}. The change lasts until you quit.")
    print()
    storage_panel()              # show the result right away


def set_warning_level():
    global WARN_AT_PERCENT
    print(f"\n  {GRAY}Drives this full or fuller get a warning sign next to their percentage.{RESET}")
    while True:
        ans = ask("Warn when a drive reaches what percent? (1–100)", str(WARN_AT_PERCENT)).rstrip("%").strip()
        if not ans.isdigit():
            fail("Enter a whole number, like 85.")
        elif not 1 <= int(ans) <= 100:
            fail("Enter a number from 1 to 100.")
        else:
            break
    old, WARN_AT_PERCENT = WARN_AT_PERCENT, int(ans)
    _save_and_report(f"Warning set to {BOLD}{WARN_AT_PERCENT}%{RESET} {GRAY}(was {old}%){RESET}")


def set_sort_order():
    global SORT_DRIVES
    keys = list(SORT_OPTIONS)
    lines = []
    for i, k in enumerate(keys, 1):
        current = f"   {GREEN}● current{RESET}" if k == SORT_DRIVES else ""
        lines.append(f"{CYAN}{BOLD}{i}{RESET}  {SORT_OPTIONS[k]}{current}")
    lines.append(f"{GRAY}b  Back{RESET}")
    print()
    print(box(lines, title="Sort drives by"))
    while True:
        ans = ask("How should drives be ordered?").lower()
        if ans in ("", "b", "back", "q"):
            return
        if ans.isdigit() and 1 <= int(ans) <= len(keys):
            break
        fail(f"Pick 1–{len(keys)} or b.")
    SORT_DRIVES = keys[int(ans) - 1]
    _save_and_report(f"Drives now sorted by {BOLD}{SORT_OPTIONS[SORT_DRIVES]}{RESET}")


def settings_menu():
    while True:
        section("Settings")
        print(box([f"{CYAN}{BOLD}1{RESET}  Drive-full warning level   "
                   f"{_pct_color(WARN_AT_PERCENT / 100)}{WARN_AT_PERCENT}%{RESET}",
                   f"{CYAN}{BOLD}2{RESET}  Sort drives by             {BOLD}{SORT_OPTIONS[SORT_DRIVES]}{RESET}",
                   f"{GRAY}b  Back to menu{RESET}"], title="Settings"))
        choice = ask("Choose a setting").lower()
        if choice in ("", "b", "back", "q"):
            return
        if choice == "1":
            set_warning_level()
        elif choice == "2":
            set_sort_order()
        else:
            fail("Pick 1, 2 or b.")


def fit_cards():
    """Size every card to the widest one (banner, storage, menu, drive table)."""
    global CARD_W
    blocks = [banner_lines(), MENU_LINES, storage_lines() or []]
    try:
        fu, ft = read_fstab()
        blocks.append(drive_table_lines(get_drives(), fu, ft)[0])
    except Exception:
        pass
    CARD_W = max([CARD_W] + [vlen(l) for b in blocks for l in b])
    # Match the big title's width too, as long as the terminal is wide enough for it.
    title_w = max(vlen(r) for r in big_title_lines(TITLE_TEXT))
    if title_w <= shutil.get_terminal_size((120, 24)).columns - 2:
        CARD_W = max(CARD_W, title_w - 4)


def banner():
    print()
    print(box(banner_lines(), color=ORANGE))


def main_menu():
    load_settings()
    fit_cards()
    startup_screen()
    title_banner()
    banner()
    actions = {"1": mount_drives, "2": setup_samba, "3": show_status, "4": settings_menu}
    while True:
        storage_panel()
        print()
        print(box(MENU_LINES, title="Menu"))
        choice = ask("Choose an option").lower()
        if choice in ("q", "quit", "exit"):
            print(f"\n  {GRAY}Bye.{RESET}\n")
            return
        if choice in actions:
            actions[choice]()
        else:
            fail("Pick 1, 2, 3, 4 or q.")


if __name__ == "__main__":
    if os.geteuid() != 0:
        print()
        print(box([f"This tool changes system files, so it needs sudo.",
                   f"Run: {BOLD}sudo {sys.argv[0]}{RESET}"], title="Permission needed", color=RED))
        print()
        sys.exit(1)
    try:
        main_menu()
    except (KeyboardInterrupt, EOFError):
        print(f"\n\n  {GRAY}Cancelled.{RESET}\n")
        sys.exit(130)
