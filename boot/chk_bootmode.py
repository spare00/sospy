#!/usr/bin/env python3
"""
chk_bootmode.py — Determine boot mode (UEFI vs Legacy BIOS) from a sosreport.

Usage:
  # Run inside an extracted sosreport directory
  python chk_bootmode.py

  # Or point to a sosreport directory or tar archive
  python chk_bootmode.py /path/to/sosreport
"""

import os
import re
import sys
import tarfile
from typing import Optional, List, Tuple

# ----------- Sosreport reader (directory or tar) -----------

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
            # Try direct
            p = os.path.join(self.root, relpath)
            if os.path.isfile(p):
                try:
                    return open(p, "r", errors="replace").read()
                except Exception:
                    return None
            # Suffix search
            for dirpath, _, filenames in os.walk(self.root):
                for fn in filenames:
                    full = os.path.join(dirpath, fn)
                    if full.endswith("/" + relpath) or full.endswith(relpath):
                        try:
                            return open(full, "r", errors="replace").read()
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

# ----------- Detection logic -----------

STRONG_UEFI = 5
STRONG_BIOS = 5
WEAK_UEFI   = 2

def scan_efibootmgr(text: str, evidence: List[str]) -> int:
    score = 0
    if "BootOrder" in text or "BootCurrent" in text:
        evidence.append("efibootmgr: BootOrder/BootCurrent found → UEFI")
        score += STRONG_UEFI
    if "EFI variables are not supported on this system" in text:
        evidence.append("efibootmgr: EFI variables not supported → BIOS")
        score -= STRONG_BIOS
    return score

def scan_mounts(text: str, evidence: List[str]) -> int:
    score = 0
    if "efivarfs on /sys/firmware/efi/efivars" in text:
        evidence.append("mounts: efivarfs mounted → UEFI")
        score += STRONG_UEFI
    if re.search(r"/boot/efi\s+type\s+vfat", text):
        evidence.append("mounts: /boot/efi mounted (weak indicator)")
        score += WEAK_UEFI
    return score

def scan_dmesg(text: str, evidence: List[str]) -> int:
    score = 0
    if re.search(r"EFI v[\d\.]+", text) or "EFI: Loaded cert" in text:
        evidence.append("dmesg: EFI firmware/loaded cert seen → UEFI")
        score += STRONG_UEFI
    if re.search(r"Secure boot (enabled|disabled)", text, re.I):
        state = re.search(r"Secure boot (enabled|disabled)", text, re.I).group(1)
        evidence.append(f"dmesg: Secure boot {state}")
        score += STRONG_UEFI
    return score

def scan_sysfs(text: str, evidence: List[str]) -> int:
    score = 0
    if "/sys/firmware/efi:" in text or " efivars" in text:
        evidence.append("ls /sys/firmware shows efi → UEFI")
        score += STRONG_UEFI
    return score

def detect(reader: SosReader) -> Tuple[str, List[str]]:
    evidence: List[str] = []
    score = 0
    # Candidate files in sosreport
    candidates = {
        "efibootmgr": ["sos_commands/boot/efibootmgr_-v"],
        "mounts": ["sos_commands/filesys/mount"],
        "dmesg": ["sos_commands/kernel/dmesg", "var/log/dmesg"],
        "sysfs": ["sos_commands/kernel/ls_-l_.sys.firmware"],
    }
    # Scan
    for p in candidates["efibootmgr"]:
        txt = reader.read_text(p)
        if txt:
            score += scan_efibootmgr(txt, evidence)
            break
    for p in candidates["mounts"]:
        txt = reader.read_text(p)
        if txt:
            score += scan_mounts(txt, evidence)
            break
    for p in candidates["dmesg"]:
        txt = reader.read_text(p)
        if txt:
            score += scan_dmesg(txt, evidence)
            break
    for p in candidates["sysfs"]:
        txt = reader.read_text(p)
        if txt:
            score += scan_sysfs(txt, evidence)
            break
    # Classify
    if score >= STRONG_UEFI:
        mode = "UEFI"
    elif score <= -STRONG_BIOS:
        mode = "BIOS (Legacy)"
    else:
        mode = "Unknown"
    return mode, evidence

# ----------- Main -----------

if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "."
    reader = SosReader(path)
    if not reader.ok():
        print(f"Error: {path} is not a sosreport directory or tarball", file=sys.stderr)
        sys.exit(1)
    mode, evidence = detect(reader)
    print(f"Boot mode: {mode}")
    if evidence:
        print("Evidence:")
        for e in evidence:
            print(" -", e)
