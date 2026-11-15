# Network Drive

A terminal tool for Linux servers (Ubuntu/Debian, Fedora, Arch, openSUSE) that **mounts drives automatically at boot** and **shares them on your network with Samba**, all from a simple menu.

- Shows every data drive with a color storage gauge, and a ⚠ warning once a drive passes 85% full
- Lists every address the server can be reached on (LAN, backup port, Tailscale)
- Mounts new drives safely: backs up `/etc/fstab` first, and uses `nofail` so a missing drive never stops the server from booting
- Shares drives as guest (no-password) Samba shares and shows the exact `\\address\share` path to use from Windows
- Skips system partitions (`/boot`, LVM, swap) so they can't be mounted or shared by mistake

## Install

One line, straight from GitHub:

```bash
curl -fsSL https://raw.githubusercontent.com/jdjones999/Network-Drive/main/install.sh | sudo bash
```

Or from a clone:

```bash
git clone https://github.com/jdjones999/Network-Drive.git
cd Network-Drive
sudo bash install.sh
```

The installer:

1. Installs what's needed: `python3`, `figlet`, `samba`, `ntfs-3g`, `curl`, and disk/network tools
2. Downloads the `rowancap` figlet font from [figlet.org](http://www.figlet.org/fonts/rowancap.flf) and puts it in figlet's font folder for your system
3. Installs the tool as `/usr/local/bin/samba-setup` (owner root, permissions 755)
4. Sets up, enables and starts Samba, turns on guest access, and opens the firewall (`ufw` or `firewalld`), plus SELinux on Fedora/RHEL
5. Checks everything and prints a ✔ / ✖ summary

Running it again is safe. It updates the tool and skips anything already done.

## Use

```bash
sudo samba-setup
```

| Option | What it does |
|---|---|
| **1** Mount drives at boot | Pick a drive, name its folder under `/mnt`, and it's added to `/etc/fstab` and mounted |
| **2** Share drives on the network | Pick a mounted drive and it becomes a Samba share |
| **3** Show drives & shares | Overview only. Changes nothing |
| **4** Settings | Set the drive-full warning percentage and the order drives are listed in: name, fullness or capacity (saved in `/etc/network-drive/settings.conf`) |

From Windows, open File Explorer and type the share address shown, for example `\\192.168.0.3\FreeAgent`.

## Settings

Open `/usr/local/bin/samba-setup` (or `samba-setup.py` in this repo) and change these near the top of their sections:

| Setting | Default | What it controls |
|---|---|---|
| `TITLE_TEXT` | `"NETWORK DRIVE"` | Title text |
| `TITLE_FONT` | `"rowancap"` | figlet font for the title |
| `TITLE_COLOR` | `208` | Title color (xterm-256 color number) |
| `STARTUP_BLANK_LINES` | `75` | Blank lines printed on start (keeps your scrollback) |
| `STARTUP_DELAY` | `0` | Seconds to pause on start |
| `WARN_AT_PERCENT` | `85` | When ⚠ appears next to a drive. Easier: change it from **4 Settings** in the menu |
| `SORT_DRIVES` | `"name"` | Drive order: `name`, `usage` (least → most full) or `capacity` (smallest → largest). Also in **4 Settings** |
| `BAR_STYLE` | `"grid"` | `grid`, `lines`, `gaps`, or `blocks` |
| `TERMINAL_BG` | `(12, 12, 12)` | Your terminal's background color, used for the gaps in the bars |

Reinstalling replaces the installed copy (the old one is kept as `samba-setup.bak`), so make lasting changes in the repo copy.

## Requirements

- A systemd Linux with `apt` (Ubuntu/Debian), `dnf` (Fedora; RHEL-family needs EPEL), `pacman` (Arch) or `zypper` (openSUSE)
- `sudo` access
- A terminal with 24-bit color and Unicode, such as Windows Terminal. The title needs a window about 120 characters wide.

## Safety notes

- Shares are **guest-accessible with write access**: anyone on your network can read and change files in them. Only use this on a network you trust.
- Every change to `/etc/fstab` and `/etc/samba/smb.conf` is backed up first with a timestamp (for example `/etc/fstab.bak-20261115-134500`).

## License

[MIT](LICENSE) © 2026 jdjones999

The `rowancap` figlet font is a separate work by its original author. The installer downloads it from figlet.org, and it is not covered by this project's license.
