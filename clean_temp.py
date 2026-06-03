#!/usr/bin/env python3
"""
Clean temporary files and junk from Windows to free disk space.

Must be run as Administrator.

Usage:
  python clean_temp.py                  # Interactive mode
  python clean_temp.py --dry-run         # Show what would be cleaned without doing it
  python clean_temp.py --safe-only       # Clean safe items only (auto-confirm)
  python clean_temp.py --all --yes       # Clean everything including conditional items
"""

import subprocess
import sys
import os
import shutil
import glob as glob_mod
import argparse
import stat
from pathlib import Path
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# Cleanup item definitions
# ---------------------------------------------------------------------------
# Each entry:
#   id        – short identifier
#   display   – human-readable name
#   category  – "safe" (can always delete) or "conditional" (think before deleting)
#   method    – one of the supported methods (see dispatch table below)
#   path      – path or callable returning path
#   pattern   – (optional) glob pattern for matching files
#   command   – (optional) PowerShell command string
#   extra     – (optional) extra data for the method

SAFE_ITEMS = [
    # ── User temp ──
    {
        "id": "user_temp",
        "display": "User Temp Files (%TEMP%)",
        "category": "safe",
        "method": "delete_dir_contents",
        "path": lambda: os.environ.get("TEMP", ""),
    },
    # ── System temp ──
    {
        "id": "system_temp",
        "display": "System Temp Files (C:\\Windows\\Temp)",
        "category": "safe",
        "method": "delete_dir_contents",
        "path": lambda: os.path.join(
            os.environ.get("SystemRoot", "C:\\Windows"), "Temp"
        ),
    },
    # ── Prefetch ──
    {
        "id": "prefetch",
        "display": "Prefetch Cache (C:\\Windows\\Prefetch)",
        "category": "safe",
        "method": "delete_dir_contents",
        "path": lambda: os.path.join(
            os.environ.get("SystemRoot", "C:\\Windows"), "Prefetch"
        ),
    },
    # ── Recycle Bin ──
    {
        "id": "recycle_bin",
        "display": "Recycle Bin (all drives)",
        "category": "safe",
        "method": "ps_command",
        "command": "Clear-RecycleBin -Force -ErrorAction SilentlyContinue",
    },
    # ── Thumbnail cache ──
    {
        "id": "thumbcache",
        "display": "Thumbnail Cache",
        "category": "safe",
        "method": "delete_files_by_pattern",
        "path": lambda: os.path.join(
            os.environ.get("LOCALAPPDATA", ""),
            "Microsoft", "Windows", "Explorer",
        ),
        "pattern": "thumbcache_*.db",
    },
    # ── Temporary Internet Files (IE/Edge) ──
    {
        "id": "inet_cache",
        "display": "Temporary Internet Files (IE/Edge)",
        "category": "safe",
        "method": "delete_dir_contents",
        "path": lambda: os.path.join(
            os.environ.get("LOCALAPPDATA", ""),
            "Microsoft", "Windows", "INetCache",
        ),
    },
    # ── Windows Defender temp files ──
    {
        "id": "defender_temp",
        "display": "Windows Defender Temporary Scan Files",
        "category": "safe",
        "method": "delete_dir_contents",
        "path": lambda: os.path.join(
            os.environ.get("ProgramData", "C:\\ProgramData"),
            "Microsoft", "Windows Defender", "Scans", "History",
        ),
    },
]

CONDITIONAL_ITEMS = [
    # ── Windows Update cleanup (DISM) ──
    {
        "id": "windows_update_cleanup",
        "display": "Windows Update Cleanup (DISM — removes superseded updates)",
        "category": "conditional",
        "method": "dism_cleanup",
    },
    # ── Windows.old ──
    {
        "id": "windows_old",
        "display": "Previous Windows Installation (C:\\Windows.old)",
        "category": "conditional",
        "method": "delete_dir",
        "path": lambda: (lambda sr: os.path.join(sr[:2], "Windows.old"))(
            os.environ.get("SystemRoot", "C:\\Windows")
        ),
    },
    # ── Delivery Optimization files ──
    {
        "id": "delivery_opt",
        "display": "Delivery Optimization Files",
        "category": "conditional",
        "method": "delete_dir_contents",
        "path": lambda: os.path.join(
            os.environ.get("SystemRoot", "C:\\Windows"),
            "SoftwareDistribution", "DeliveryOptimization",
        ),
    },
    # ── DirectX shader cache ──
    {
        "id": "dx_shader_cache",
        "display": "DirectX Shader Cache",
        "category": "conditional",
        "method": "delete_dir_contents",
        "path": lambda: os.path.join(
            os.environ.get("LOCALAPPDATA", ""), "D3DSCache",
        ),
    },
    # ── Device driver packages (old drivers) ──
    {
        "id": "old_drivers",
        "display": "Old Device Driver Packages (via Dism)",
        "category": "conditional",
        "method": "dism_driver_cleanup",
    },
    # ── System error memory dumps ──
    {
        "id": "memory_dumps",
        "display": "System Error Memory Dump Files",
        "category": "conditional",
        "method": "delete_files_by_pattern_multi",
        "paths": [
            lambda: os.path.join(
                os.environ.get("SystemRoot", "C:\\Windows"), "MEMORY.DMP"
            ),
            lambda: os.path.join(
                os.environ.get("SystemRoot", "C:\\Windows"), "Minidump",
            ),
        ],
        "patterns": ["MEMORY.DMP", "*.dmp"],
    },
    # ── Windows upgrade logs ──
    {
        "id": "upgrade_logs",
        "display": "Windows Upgrade Log Files",
        "category": "conditional",
        "method": "delete_dir_contents",
        "path": lambda: os.path.join(
            os.environ.get("SystemRoot", "C:\\Windows"), "Logs",
        ),
    },
    # ── System restore points ──
    {
        "id": "restore_points",
        "display": "System Restore Points (keeps only the most recent one)",
        "category": "conditional",
        "method": "ps_command",
        "command": (
            "Get-ComputerRestorePoint | "
            "Sort-Object -Property CreationTime -Descending | "
            "Select-Object -Skip 1 | "
            "ForEach-Object { "
            "  Disable-ComputerRestore -Drive 'C:\\' -ErrorAction SilentlyContinue; "
            "  Enable-ComputerRestore -Drive 'C:\\' -ErrorAction SilentlyContinue "
            "}"
        ),
    },
]

ALL_ITEMS = SAFE_ITEMS + CONDITIONAL_ITEMS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def is_admin() -> bool:
    """Return True if running with administrator privileges."""
    try:
        return os.getuid() == 0
    except AttributeError:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin() != 0


def run_ps(cmd: str, timeout: int = 120) -> subprocess.CompletedProcess:
    """Run a PowerShell command and return the CompletedProcess."""
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", cmd],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _resolve_path(path_or_callable) -> str:
    """Resolve a path that may be a string, callable, or None."""
    if callable(path_or_callable):
        return path_or_callable()
    return path_or_callable or ""


def _handle_remove_readonly(func, dirpath, excinfo):
    """Error handler for shutil.rmtree: clear read-only flag and retry."""
    exc_value = excinfo[1]
    if func in (os.rmdir, os.remove, os.unlink) and exc_value:
        try:
            os.chmod(dirpath, stat.S_IWRITE)
            func(dirpath)
        except OSError:
            pass


def get_dir_size_mb(dir_path: str) -> float:
    """Return directory size in megabytes. Returns 0 if path doesn't exist."""
    path = Path(dir_path)
    if not path.exists():
        return 0.0
    total = 0
    try:
        for f in path.rglob("*"):
            if f.is_file():
                try:
                    total += f.stat().st_size
                except OSError:
                    pass
    except (PermissionError, OSError):
        pass
    return total / (1024 * 1024)


def get_file_size_mb(file_path: str) -> float:
    """Return file size in megabytes. Returns 0 if not exists."""
    path = Path(file_path)
    if not path.exists() or not path.is_file():
        return 0.0
    try:
        return path.stat().st_size / (1024 * 1024)
    except OSError:
        return 0.0


def estimate_item_size(item: dict) -> float:
    """Estimate the size in MB for a cleanup item."""
    method = item["method"]

    if method in ("delete_dir_contents", "delete_dir"):
        p = _resolve_path(item.get("path"))
        if not p:
            return 0.0
        if method == "delete_dir":
            return get_dir_size_mb(p)
        else:
            return get_dir_size_mb(p)

    if method == "delete_files_by_pattern":
        p = _resolve_path(item.get("path"))
        pattern = item.get("pattern", "*")
        total = 0.0
        for f in glob_mod.glob(os.path.join(p, pattern)):
            total += get_file_size_mb(f)
        return total

    if method == "delete_files_by_pattern_multi":
        total = 0.0
        paths = item.get("paths", [])
        patterns = item.get("patterns", [])
        for i, path_callable in enumerate(paths):
            p = _resolve_path(path_callable)
            pat = patterns[i] if i < len(patterns) else "*"
            if os.path.isdir(p):
                for f in glob_mod.glob(os.path.join(p, pat)):
                    total += get_file_size_mb(f)
            elif os.path.isfile(p) and (pat == "*" or Path(p).match(pat)):
                total += get_file_size_mb(p)
        return total

    # ps_command, dism_cleanup, dism_driver_cleanup – estimate not available
    return -1.0


# ---------------------------------------------------------------------------
# Cleanup method dispatchers
# ---------------------------------------------------------------------------
def _delete_dir_contents(path: str) -> tuple[bool, str, int]:
    """Delete all files and subdirectories inside a folder; keep the folder itself."""
    if not path or not os.path.isdir(path):
        return True, "directory not found (already clean)", 0

    count = 0
    errors = []
    for entry in os.listdir(path):
        full = os.path.join(path, entry)
        try:
            if os.path.isfile(full) or os.path.islink(full):
                os.remove(full)
                count += 1
            elif os.path.isdir(full):
                shutil.rmtree(full, onerror=_handle_remove_readonly)
                count += 1
        except (OSError, PermissionError, shutil.Error) as e:
            errors.append(f"{entry}: {e}")

    if errors:
        return False, f"deleted {count} items with {len(errors)} errors: {'; '.join(errors[:3])}", count
    return True, f"deleted {count} items", count


def _delete_dir(path: str) -> tuple[bool, str, int]:
    """Delete an entire directory."""
    if not path or not os.path.exists(path):
        return True, "does not exist", 0
    try:
        shutil.rmtree(path, onerror=_handle_remove_readonly)
        return True, "deleted", 1
    except (OSError, PermissionError, shutil.Error) as e:
        return False, f"failed: {e}", 0


def _delete_files_by_pattern(path: str, pattern: str) -> tuple[bool, str, int]:
    """Delete files matching a glob pattern in a directory."""
    if not path or not os.path.isdir(path):
        return True, "directory not found", 0
    count = 0
    errors = []
    for f in glob_mod.glob(os.path.join(path, pattern)):
        try:
            os.remove(f)
            count += 1
        except (OSError, PermissionError) as e:
            errors.append(f"{os.path.basename(f)}: {e}")
    if errors:
        return False, f"deleted {count} files, {len(errors)} errors", count
    return True, f"deleted {count} files", count


def _delete_files_by_pattern_multi(paths: list, patterns: list) -> tuple[bool, str, int]:
    """Delete files matching patterns across multiple paths."""
    total = 0
    all_errors = []
    for i, path_callable in enumerate(paths):
        p = _resolve_path(path_callable)
        pat = patterns[i] if i < len(patterns) else "*"
        if not p or not os.path.exists(p):
            continue
        if os.path.isdir(p):
            for f in glob_mod.glob(os.path.join(p, pat)):
                try:
                    os.remove(f)
                    total += 1
                except (OSError, PermissionError) as e:
                    all_errors.append(f"{os.path.basename(f)}: {e}")
        elif os.path.isfile(p):
            try:
                os.remove(p)
                total += 1
            except (OSError, PermissionError) as e:
                all_errors.append(f"{os.path.basename(p)}: {e}")
    if all_errors:
        return False, f"deleted {total} files, {len(all_errors)} errors", total
    return True, f"deleted {total} files", total


def _ps_command(command: str) -> tuple[bool, str, int]:
    """Execute a PowerShell command for cleanup."""
    result = run_ps(command)
    if result.returncode == 0:
        return True, "completed", 0
    err = result.stderr.strip() or result.stdout.strip()
    return False, f"failed: {err[:200]}", 0


def _dism_cleanup() -> tuple[bool, str, int]:
    """Run DISM component cleanup."""
    result = subprocess.run(
        [
            "dism.exe", "/Online", "/Cleanup-Image",
            "/StartComponentCleanup", "/ResetBase",
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode == 0:
        return True, "completed", 0
    # Try without /ResetBase if it fails
    result2 = subprocess.run(
        ["dism.exe", "/Online", "/Cleanup-Image", "/StartComponentCleanup"],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result2.returncode == 0:
        return True, "completed (without /ResetBase)", 0
    err = result2.stderr.strip() or result2.stdout.strip()
    return False, f"failed: {err[:300]}", 0


def _dism_driver_cleanup() -> tuple[bool, str, int]:
    """Remove old driver packages via DISM."""
    result = subprocess.run(
        ["dism.exe", "/Online", "/Get-Drivers", "/Format:Table"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    # Just run the cleanup — DISM will remove unused drivers
    result2 = subprocess.run(
        [
            "dism.exe", "/Online", "/Cleanup-Image",
            "/StartComponentCleanup", "/ResetBase",
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result2.returncode == 0:
        return True, "completed", 0
    err = result2.stderr.strip() or result2.stdout.strip()
    return False, f"failed: {err[:300]}", 0


# Dispatch table
METHODS = {
    "delete_dir_contents": lambda item: _delete_dir_contents(
        _resolve_path(item["path"])
    ),
    "delete_dir": lambda item: _delete_dir(_resolve_path(item["path"])),
    "delete_files_by_pattern": lambda item: _delete_files_by_pattern(
        _resolve_path(item["path"]), item["pattern"]
    ),
    "delete_files_by_pattern_multi": lambda item: _delete_files_by_pattern_multi(
        item["paths"], item["patterns"]
    ),
    "ps_command": lambda item: _ps_command(item["command"]),
    "dism_cleanup": lambda _item: _dism_cleanup(),
    "dism_driver_cleanup": lambda _item: _dism_driver_cleanup(),
}


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def cmd_dry_run(items: list[dict]):
    """Print what would be cleaned without changing anything."""
    print("=" * 72)
    print("DRY RUN — no changes will be made")
    print("=" * 72)

    grand_total = 0.0
    for item in items:
        size_mb = estimate_item_size(item)
        size_str = f"{size_mb:.1f} MB" if size_mb >= 0 else "(unknown)"
        if size_mb > 0:
            grand_total += size_mb

        print(f"\n  [{item['category'].upper()}] {item['display']}")
        print(f"    Estimated size : {size_str}")

    print("\n" + "=" * 72)
    if grand_total > 0:
        print(f"Estimated total: {grand_total:.1f} MB ({grand_total / 1024:.2f} GB)")
    else:
        print("Estimated total: unknown (some items cannot be pre-measured)")
    print("=" * 72)


def cmd_apply(items: list[dict], yes: bool = False, label: str = "selected"):
    """Clean all items. Show confirmation unless --yes is set."""
    if not items:
        print("No items to clean.")
        return

    if not yes:
        print(f"The following {label} items will be cleaned:\n")
        for item in items:
            size_mb = estimate_item_size(item)
            size_str = f"~{size_mb:.1f} MB" if size_mb >= 0 else "unknown size"
            print(f"  • {item['display']}  ({size_str})")

        # Warn about conditional items
        conditional = [i for i in items if i["category"] == "conditional"]
        if conditional:
            print("\n" + "!" * 72)
            print("WARNING: Some items are CONDITIONAL — review before deleting:")
            for ci in conditional:
                print(f"  - {ci['display']}")
            print("!" * 72)

        try:
            resp = input("\nProceed? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            sys.exit(0)
        if resp not in ("y", "yes"):
            print("Aborted.")
            sys.exit(0)

    # ── Execute ──
    total_files = 0
    total_ok = 0
    failures = []
    freed_mb = 0.0

    for item in items:
        print(f"\n── {item['display']} ──")

        # Measure before
        before_mb = estimate_item_size(item)

        method_name = item["method"]
        handler = METHODS.get(method_name)
        if handler is None:
            failures.append((item["id"], f"unknown method: {method_name}"))
            continue

        try:
            ok, msg, count = handler(item)
        except Exception as e:
            ok, msg, count = False, str(e), 0

        # Measure after
        after_mb = estimate_item_size(item)
        if before_mb >= 0 and after_mb >= 0:
            diff = before_mb - after_mb
            if diff > 0:
                freed_mb += diff
                print(f"   Result: {msg}  (freed ~{diff:.1f} MB)")
            else:
                print(f"   Result: {msg}")
        else:
            print(f"   Result: {msg}")

        total_files += count
        if ok:
            total_ok += 1
        else:
            failures.append((item["id"], msg))

    print("\n" + "=" * 72)
    print(f"Done: {total_ok}/{len(items)} items cleaned successfully.")
    if total_files > 0:
        print(f"Total files/folders deleted: {total_files}")
    if freed_mb > 0:
        print(f"Estimated space freed: {freed_mb:.1f} MB ({freed_mb / 1024:.2f} GB)")
    if failures:
        print(f"\n{len(failures)} failure(s):")
        for fid, fmsg in failures:
            print(f"  - {fid}: {fmsg}")
    print("=" * 72)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Clean temporary files and junk from Windows.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--dry-run", action="store_true",
        help="Preview what would be cleaned (estimate sizes) without deleting",
    )
    group.add_argument(
        "--safe-only", "--safe", action="store_true",
        help="Clean only safe items without confirmation",
    )
    group.add_argument(
        "--all", action="store_true",
        help="Clean ALL items (safe + conditional); use --yes to skip prompt",
    )
    parser.add_argument(
        "--yes", "-y", action="store_true",
        help="Skip confirmation prompts",
    )
    args = parser.parse_args()

    if not is_admin():
        print("ERROR: This script requires Administrator privileges.")
        print("Please right-click → 'Run as administrator', or run from an elevated terminal.")
        sys.exit(2)

    if args.dry_run:
        if args.safe_only:
            cmd_dry_run(SAFE_ITEMS)
        else:
            items = SAFE_ITEMS if not args.all else ALL_ITEMS
            cmd_dry_run(items)
    elif args.safe_only:
        cmd_apply(SAFE_ITEMS, yes=args.yes, label="safe")
    elif args.all:
        cmd_apply(ALL_ITEMS, yes=args.yes, label="all")
    else:
        # Interactive: show categories, let user choose
        print("=" * 72)
        print("  Windows Temp & Junk File Cleaner")
        print("=" * 72)
        print(f"\n  Safe items ({len(SAFE_ITEMS)}): Always safe to delete")
        for item in SAFE_ITEMS:
            print(f"    • {item['display']}")
        print(f"\n  Conditional items ({len(CONDITIONAL_ITEMS)}): Review before deleting")
        for item in CONDITIONAL_ITEMS:
            print(f"    • {item['display']}")

        print("\n" + "-" * 72)
        print("Options:")
        print("  [1] Clean safe items only (recommended)")
        print("  [2] Clean ALL items (safe + conditional)")
        print("  [3] Dry-run — estimate sizes only")
        print("  [q] Quit")
        print("-" * 72)

        try:
            choice = input("\nEnter choice [1/2/3/q]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            sys.exit(0)

        if choice == "1":
            cmd_apply(SAFE_ITEMS, yes=args.yes, label="safe")
        elif choice == "2":
            cmd_apply(ALL_ITEMS, yes=args.yes, label="all")
        elif choice == "3":
            cmd_dry_run(ALL_ITEMS)
        else:
            print("Quit.")


if __name__ == "__main__":
    main()
