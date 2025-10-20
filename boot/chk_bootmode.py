#!/usr/bin/env python3
"""
chk_bootmode.py — Determine boot mode (UEFI vs Legacy BIOS) and Secure Boot state from a sosreport.
It does not execute any commands; it only inspects files inside a sosreport archive or directory.

Features:
  * Detect UEFI vs BIOS from mounts, findmnt, dmesg, sysfs.
  * Use sos.json and sos.log for hints.
  * Parse captured mokutil output (sos_commands/boot/mokutil_--sb-state).
  * Report Secure Boot status: enabled / disabled / unknown / not-applicable.
  * Verbose (-v) shows evidence lines.

Usage:
  ./chk_bootmode.py [-v] [sosreport-dir|sosreport.tar.xz]

Exit codes:
  0 -> Determined (UEFI or BIOS)
  2 -> Unknown
"""

import os
import re
import sys
import tarfile
import json
from typing import Optional, List, Tuple, Dict

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

# --- scanners ---

def scan_efibootmgr(text: str, ev: List[str]) -> int:
    score = 0
    if not text:
        return score
    if re.search(r"\bBoot(Order|Current)\b", text):
        ev.append("efibootmgr: Found BootOrder/BootCurrent → UEFI")
        score += STRONG_UEFI
    if "EFI variables are not supported on this system" in text:
        ev.append("efibootmgr: 'EFI variables are not supported' → BIOS")
        score -= STRONG_BIOS
    return score

def scan_mokutil_file(text: str, ev: List[str]) -> str:
    if not text:
        return "unknown"
    m = re.search(r"\bSecureBoot\s+(enabled|disabled)\b", text, re.I)
    if m:
        state = m.group(1).lower()
        ev.append(f"mokutil file: SecureBoot {state}")
        return state
    if "EFI variables are not supported on this system" in text:
        ev.append("mokutil file: 'EFI variables are not supported' (BIOS)")
        return "bios"
    return "unknown"

def scan_mounts_text(text: str, ev: List[str]) -> int:
    score = 0
    if not text:
        return score
    if re.search(r"\befivarfs on /sys/firmware/efi/efivars\b", text):
        ev.append("mounts: efivarfs mounted at /sys/firmware/efi/efivars → UEFI")
        score += STRONG_UEFI
    if re.search(r"\s/boot/efi\s+type\s+vfat\b", text) or re.search(r"\s/efi\s+type\s+vfat\b", text):
        ev.append("mounts: /boot/efi or /efi VFAT mounted (weak indicator)")
        score += WEAK_UEFI
    if re.search(r"^systemd-1 on /efi type autofs\b", text, re.M):
        ev.append("mounts: systemd-1 on /efi autofs (common in BIOS boots)")
        score -= 1
    return score

def scan_findmnt_text(text: str, ev: List[str]) -> int:
    score = 0
    if not text:
        return score
    for ln in text.splitlines():
        if "/sys/firmware/efi/efivars" in ln and "efivarfs" in ln:
            ev.append("findmnt: /sys/firmware/efi/efivars (efivarfs) → UEFI")
            score += STRONG_UEFI
            break
    for ln in text.splitlines():
        if ("/boot/efi" in ln or "/efi" in ln) and "vfat" in ln:
            ev.append("findmnt: /boot/efi or /efi (vfat) present (weak indicator)")
            score += WEAK_UEFI
            break
    return score

def scan_dmesg(text: str, ev: List[str]) -> Tuple[int, Optional[str]]:
    score = 0
    sb_state = None
    if not text:
        return score, sb_state
    if re.search(r"\bEFI v[\d\.]+", text) or "EFI: Loaded cert" in text or re.search(r"\befi:\s", text, re.I):
        ev.append("dmesg: EFI runtime / cert messages present → UEFI")
        score += STRONG_UEFI
    m = re.search(r"\bSecure boot (enabled|disabled)\b", text, re.I)
    if m:
        sb_state = m.group(1).lower()
        ev.append(f"dmesg: Secure boot {sb_state}")
        score += STRONG_UEFI
    return score, sb_state

def scan_sysfs_listing(text: str, ev: List[str]) -> int:
    if not text:
        return 0
    if "efi" in text:
        ev.append("ls /sys/firmware: efi present → UEFI")
        return STRONG_UEFI
    return 0

def parse_sos_json(text: str) -> Dict[str, Dict]:
    out: Dict[str, Dict] = {}
    try:
        obj = json.loads(text)
    except Exception:
        return out
    def walk(o):
        if isinstance(o, dict):
            if "name" in o and "return_code" in o:
                out[o["name"]] = {"href": o.get("href"), "return_code": o.get("return_code")}
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o: walk(v)
    walk(obj)
    return out

def scan_sos_logs(text: str, ev: List[str]) -> int:
    score = 0
    if not text:
        return score
    if "EFI variables are not supported on this system" in text:
        ev.append("sos.log: 'EFI variables are not supported' observed → BIOS")
        score -= STRONG_BIOS
    if "collecting output of 'efibootmgr -v'" in text:
        ev.append("sos.log: efibootmgr -v was executed (output may be elsewhere)")
    if "collecting output of 'mokutil --sb-state'" in text:
        ev.append("sos.log: mokutil --sb-state was executed (output may be elsewhere)")
    return score

# --- detection ---

def detect(reader: SosReader) -> Tuple[str, str, List[str], int]:
    evidence: List[str] = []
    score = 0
    dmesg_sb = None
    mokutil_sb = None

    candidates = {
        "efibootmgr": ["sos_commands/boot/efibootmgr_-v", "sos_commands/boot/efibootmgr"],
        "mokutil_file": ["sos_commands/boot/mokutil_--sb-state"],
        "mounts": ["sos_commands/filesys/mount_-l", "sos_commands/filesys/mount"],
        "findmnt": ["sos_commands/filesys/findmnt"],
        "dmesg": ["sos_commands/kernel/dmesg", "sos_commands/kernel/dmesg_-T", "var/log/dmesg"],
        "sysfs": ["sos_commands/kernel/ls_-l_.sys.firmware"],
        "sos_json": ["sos_reports/sos.json"],
        "sos_log": ["sos_logs/sos.log"],
    }

    for p in candidates["efibootmgr"]:
        txt = reader.read_text(p)
        if txt:
            score += scan_efibootmgr(txt, evidence)
            break

    for p in candidates["mokutil_file"]:
        txt = reader.read_text(p)
        if txt:
            mokutil_sb = scan_mokutil_file(txt, evidence)
            if mokutil_sb in {"enabled","disabled"}:
                score += STRONG_UEFI
            elif mokutil_sb == "bios":
                score -= STRONG_BIOS
            break

    for p in candidates["mounts"]:
        txt = reader.read_text(p)
        if txt:
            score += scan_mounts_text(txt, evidence)
            break

    for p in candidates["findmnt"]:
        txt = reader.read_text(p)
        if txt:
            score += scan_findmnt_text(txt, evidence)
            break

    for p in candidates["dmesg"]:
        txt = reader.read_text(p)
        if txt:
            s, sb = scan_dmesg(txt, evidence)
            score += s
            if sb: dmesg_sb = sb
            break

    for p in candidates["sysfs"]:
        txt = reader.read_text(p)
        if txt:
            score += scan_sysfs_listing(txt, evidence)
            break

    for p in candidates["sos_json"]:
        txt = reader.read_text(p)
        if txt:
            meta = parse_sos_json(txt)
            for cmd in ("efibootmgr -v", "mokutil --sb-state"):
                if cmd in meta:
                    rc = meta[cmd].get("return_code")
                    evidence.append(f"sos.json: '{cmd}' return_code={rc}")
            break

    for p in candidates["sos_log"]:
        txt = reader.read_text(p)
        if txt:
            score += scan_sos_logs(txt, evidence)
            break

    if score >= STRONG_UEFI:
        mode, rc = "UEFI", 0
    elif score <= -STRONG_BIOS:
        mode, rc = "BIOS (Legacy)", 0
    else:
        uefi_patterns = re.compile(
            r"(?:\bEFI\b|/sys/firmware/efi|efivarfs|/boot/efi|\bBoot(Order|Current)\b)",
            re.I,
        )
        has_any_uefi_signal = any(uefi_patterns.search(e) for e in evidence)
        if not has_any_uefi_signal:
            evidence.append("Heuristic: no EFI signals in mounts/findmnt/dmesg/sysfs → BIOS")
            mode, rc = "BIOS (Legacy)", 0
        else:
            mode, rc = "Unknown", 2

    if mode.startswith("BIOS"):
        sb_state = "not-applicable"
    else:
        if mokutil_sb in {"enabled","disabled"}:
            sb_state = mokutil_sb
        elif dmesg_sb in {"enabled","disabled"}:
            sb_state = dmesg_sb
        else:
            sb_state = "unknown"

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
        print(f"Error: {path} is not a sosreport", file=sys.stderr)
        sys.exit(2)

    mode, sb_state, evidence, rc = detect(reader)

    print(f"Boot mode : {mode}")
    print(f"SecureBoot: {sb_state}")

    if verbose or mode == "Unknown":
        if evidence:
            print("\nEvidence:")
            for e in evidence:
                print(f"  - {e}")

    sys.exit(rc)

if __name__ == "__main__":
    main()

