#!/usr/bin/env python3
"""
chk_bootmode.py — Determine boot mode (UEFI vs Legacy BIOS) and Secure Boot state from a sosreport.
No runtime tools (e.g., mokutil) are assumed; we only use files inside the sosreport.

Usage:
  ./chk_bootmode.py             # run inside sosreport directory
  ./chk_bootmode.py /path/to/sos  # or point to dir/tar(.gz|.xz)
  ./chk_bootmode.py -v [path]   # verbose evidence

Exit codes:
  0 -> Determined (UEFI or BIOS); Secure Boot may be unknown
  2 -> Unknown (insufficient evidence to classify boot mode)
"""

import os
import re
import sys
import tarfile
from typing import Optional, List, Tuple

STRONG_UEFI = 5
STRONG_BIOS = 5
WEAK_UEFI   = 2

class SosReader:
    def __init__(self, root: str):
        self.root = root
        self._tar = None
        if os.path.isdir(root):
            self.mode = "dir"
        else:
            try:
                self._tar = tarfile.open(root, "r:*")
                self.mode = "tar"
            except Exception:
                self.mode = None

    def ok(self) -> bool:
        return self.mode in {"dir", "tar"}

    def _tar_getmember(self, name: str) -> Optional[tarfile.TarInfo]:
        assert self._tar is not None
        if name in self._tar.getnames():
            return self._tar.getmember(name)
        for n in self._tar.getnames():
            if n.endswith("/" + name) or n.endswith(name):
                return self._tar.getmember(n)
        return None

    def read_text(self, relpath: str) -> Optional[str]:
        if self.mode == "dir":
            p = os.path.join(self.root, relpath)
            if os.path.isfile(p):
                try:
                    with open(p, "r", errors="replace") as f:
                        return f.read()
                except Exception:
                    return None
            # suffix search (sos often has a top-level dir prefix)
            for dirpath, _, filenames in os.walk(self.root):
                for fn in filenames:
                    full = os.path.join(dirpath, fn)
                    if full.endswith("/" + relpath) or full.endswith(relpath):
                        try:
                            with open(full, "r", errors="replace") as f:
                                return f.read()
                        except Exception:
                            return None
            return None
        elif self.mode == "tar":
            m = self._tar_getmember(relpath)
            if not m or not m.isfile():
                for n in self._tar.getnames():
                    if n.endswith("/" + relpath) or n.endswith(relpath):
                        m = self._tar.getmember(n)
                        break
            if not m or not m.isfile():
                return None
            f = self._tar.extractfile(m)
            if not f:
                return None
            data = f.read()
            return data.decode("utf-8", errors="replace")
        return None

# -------- scanners (no mokutil here) --------

def scan_efibootmgr(text: str, ev: List[str]) -> int:
    score = 0
    if not text:
        return score
    if re.search(r"\bBoot(Order|Current)\b", text):
        ev.append("efibootmgr: Found BootOrder/BootCurrent → UEFI")
        score += STRONG_UEFI
    if "EFI variables are not supported on this system" in text:
        ev.append("efibootmgr: EFI variables not supported → BIOS")
        score -= STRONG_BIOS
    return score

def scan_mounts(text: str, ev: List[str]) -> int:
    score = 0
    if not text:
        return score
    if re.search(r"\befivarfs on /sys/firmware/efi/efivars\b", text):
        ev.append("mounts: efivarfs mounted at /sys/firmware/efi/efivars → UEFI")
        score += STRONG_UEFI
    # Weak: /boot/efi or /efi VFAT can exist even in BIOS boots
    if re.search(r"\s/boot/efi\s+type\s+vfat\b", text) or re.search(r"\s/efi\s+type\s+vfat\b", text):
        ev.append("mounts: /boot/efi or /efi VFAT mounted (weak indicator)")
        score += WEAK_UEFI
    if re.search(r"^systemd-1 on /efi type autofs\b", text, re.M):
        ev.append("mounts: systemd-1 on /efi autofs (common in BIOS boots)")
        score -= 1
    return score

def scan_dmesg(text: str, ev: List[str]) -> Tuple[int, Optional[str]]:
    score = 0
    sb_state = None
    if not text:
        return score, sb_state
    # UEFI runtime signals
    if re.search(r"\bEFI v[\d\.]+", text) or "EFI: Loaded cert" in text or re.search(r"\befi:\s", text, re.I):
        ev.append("dmesg: EFI runtime / cert messages present → UEFI")
        score += STRONG_UEFI
    # Explicit Secure Boot state (only trust explicit strings)
    m = re.search(r"\bSecure boot (enabled|disabled)\b", text, re.I)
    if m:
        sb_state = m.group(1).lower()
        ev.append(f"dmesg: Secure boot {sb_state}")
        score += STRONG_UEFI  # implies UEFI path
    return score, sb_state

def scan_sysfs_listing(text: str, ev: List[str]) -> int:
    score = 0
    if not text:
        return score
    if re.search(r"^/sys/firmware/efi:", text, re.M) or " efivars" in text:
        ev.append("ls /sys/firmware: efi present → UEFI")
        score += STRONG_UEFI
    return score

# -------- detection --------

def detect(reader: SosReader) -> Tuple[str, str, List[str], int]:
    evidence: List[str] = []
    score = 0
    dmesg_txt = None
    dmesg_sb = None

    candidates = {
        "efibootmgr": [
            "sos_commands/boot/efibootmgr_-v",
            "sos_commands/boot/efibootmgr",
        ],
        "mounts": [
            "sos_commands/filesys/mount_-l",
            "sos_commands/filesys/mount",
            "sos_commands/filesys/findmnt_-R",
        ],
        "dmesg": [
            "sos_commands/kernel/dmesg",
            "var/log/dmesg",
        ],
        "sysfs": [
            "sos_commands/kernel/ls_-l_.sys.firmware",
            "sos_commands/filesys/ls_-alR_.sys.firmware",
            "sos_commands/kernel/ls_-l_.sys.firmware.efi",
        ],
    }

    # efibootmgr (if collected)
    for p in candidates["efibootmgr"]:
        txt = reader.read_text(p)
        if txt:
            score += scan_efibootmgr(txt, evidence)
            break

    # mounts
    mounts_seen = False
    for p in candidates["mounts"]:
        txt = reader.read_text(p)
        if txt:
            mounts_seen = True
            score += scan_mounts(txt, evidence)
            break

    # dmesg
    for p in candidates["dmesg"]:
        txt = reader.read_text(p)
        if txt:
            dmesg_txt = txt
            s, sb = scan_dmesg(txt, evidence)
            score += s
            if sb:
                dmesg_sb = sb
            break

    # sysfs listing
    for p in candidates["sysfs"]:
        txt = reader.read_text(p)
        if txt:
            score += scan_sysfs_listing(txt, evidence)
            break

    # Boot mode classification
    if score >= STRONG_UEFI:
        mode = "UEFI"
        rc = 0
    elif score <= -STRONG_BIOS:
        mode = "BIOS (Legacy)"
        rc = 0
    else:
        # If we looked at mounts/dmesg and found no EFI signals at all → assume BIOS
        has_any_uefi_signal = any(("efivarfs" in e.lower()) or ("efi runtime" in e.lower())
                                   or ("BootOrder" in e) or ("UEFI" in e)
                                   for e in evidence)
        if not has_any_uefi_signal and (mounts_seen or dmesg_txt is not None):
            evidence.append("Heuristic: no EFI signals in mounts/dmesg/sysfs → BIOS")
            mode = "BIOS (Legacy)"
            rc = 0
        else:
            mode = "Unknown"
            rc = 2

    # Secure Boot state (from dmesg only; conservative)
    if mode.startswith("BIOS"):
        sb_state = "not-applicable"
    else:
        if dmesg_sb in {"enabled", "disabled"}:
            sb_state = dmesg_sb
        else:
            sb_state = "unknown"

    # Dedup evidence
    dedup, seen = [], set()
    for e in evidence:
        if e not in seen:
            dedup.append(e); seen.add(e)

    return mode, sb_state, dedup, rc

def main():
    args = sys.argv[1:]
    verbose = False
    path = "."

    if args and args[0] == "-v":
        verbose = True
        args = args[1:]
    if args:
        path = args[0]

    reader = SosReader(path)
    if not reader.ok():
        print(f"Error: {path} is not a sosreport directory or tarball", file=sys.stderr)
        sys.exit(2)

    mode, sb_state, evidence, rc = detect(reader)

    print(f"Boot mode : {mode}")
    print(f"SecureBoot: {sb_state}")

    if verbose or mode == "Unknown":
        if evidence:
            print("\nEvidence:")
            for e in evidence:
                print(f"  - {e}")

    if mode == "Unknown":
        print("\nHints:")
        print("  * Ensure sos collected: efibootmgr, mount -l, dmesg, and ls of /sys/firmware")
        print("  * /boot/efi alone is a weak indicator; efivarfs or EFI dmesg lines are authoritative.")

    sys.exit(rc)

if __name__ == "__main__":
    main()
