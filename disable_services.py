#!/usr/bin/env python3
"""
Disable problematic Windows services that cause:
  - Black screen on boot (black screen with only mouse cursor)
  - Slow boot times

Must be run as Administrator.

Usage:
  python disable_services.py                # Interactive mode
  python disable_services.py --dry-run       # Show what would be done without doing it
  python disable_services.py --yes           # Skip confirmation, apply all
  python disable_services.py --restore       # Restore original startup types from backup
"""

import subprocess
import sys
import os
import json
import argparse
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Service definitions
# ---------------------------------------------------------------------------
# Each entry: (service_name, display_name, reason_category, condition)
SERVICES = [
    # ── Black screen on boot ──
    {
        "name": "AppReadiness",
        "display": "App Readiness",
        "reason": "Can cause black screen with only mouse cursor after boot (Explorer fails to load)",
        "category": "black_screen",
    },
    {
        "name": "SysMain",
        "display": "SysMain (Superfetch)",
        "reason": "Preloading on SSD degrades performance; may prevent Explorer from starting",
        "category": "black_screen",
    },
    {
        "name": "WSearch",
        "display": "Windows Search",
        "reason": "Constant indexing consumes CPU/disk I/O; delays desktop load",
        "category": "black_screen",
    },
    {
        "name": "DiagTrack",
        "display": "Connected User Experiences and Telemetry",
        "reason": "Background telemetry uploads waste resources",
        "category": "black_screen",
    },
    {
        "name": "dmwappushsvc",
        "display": "Device Management WAP Push Service",
        "reason": "Telemetry-related push routing; unnecessary resource drain",
        "category": "black_screen",
    },
    # ── Slow boot ──
    {
        "name": "Spooler",
        "display": "Print Spooler",
        "reason": "Unnecessary when no printer is used; wastes resources at boot",
        "category": "slow_boot",
    },
    {
        "name": "PcaSvc",
        "display": "Program Compatibility Assistant",
        "reason": "Constantly scans for compatibility issues; slows responsiveness",
        "category": "slow_boot",
    },
    {
        "name": "TermService",
        "display": "Remote Desktop Services",
        "reason": "Unused remote-desktop service consumes background resources",
        "category": "slow_boot",
    },
    {
        "name": "wcncsvc",
        "display": "Windows Connect Now",
        "reason": "Unnecessary when not configuring wireless settings",
        "category": "slow_boot",
    },
]

BACKUP_FILE = Path(__file__).with_suffix(".backup.json")


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


def run_ps(cmd: str) -> subprocess.CompletedProcess:
    """Run a PowerShell command and return the CompletedProcess."""
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", cmd],
        capture_output=True,
        text=True,
        timeout=30,
    )


def get_service_info(service_name: str) -> dict | None:
    """Query the current state and start-type of a service via PowerShell.
    Returns dict with keys: Name, DisplayName, Status, StartType, or None."""
    cmd = f"Get-Service -Name '{service_name}' -ErrorAction SilentlyContinue | Select-Object Name, DisplayName, Status, StartType | ConvertTo-Json"
    result = run_ps(cmd)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return None


def stop_service(service_name: str) -> tuple[bool, str]:
    """Try to stop a running service. Returns (success, message)."""
    info = get_service_info(service_name)
    if info is None:
        return True, "not found (may already be removed)"
    if info["Status"] == "Stopped":
        return True, "already stopped"
    result = run_ps(f"Stop-Service -Name '{service_name}' -Force -ErrorAction Stop")
    if result.returncode == 0:
        return True, "stopped successfully"
    return False, f"failed to stop: {result.stderr.strip()}"


def set_startup_type(service_name: str, start_type: str) -> tuple[bool, str]:
    """Set service startup type (Disabled, Manual, Automatic).
    Returns (success, message)."""
    valid = {"Disabled", "Manual", "Automatic"}
    if start_type not in valid:
        return False, f"invalid start type '{start_type}'"
    result = run_ps(
        f"Set-Service -Name '{service_name}' -StartupType '{start_type}' -ErrorAction Stop"
    )
    if result.returncode == 0:
        return True, f"set to {start_type}"
    # Fallback via sc.exe
    sc_map = {"Disabled": "disabled", "Manual": "demand", "Automatic": "auto"}
    fallback = subprocess.run(
        ["sc.exe", "config", service_name, "start=" + sc_map[start_type]],
        capture_output=True, text=True, timeout=15,
    )
    if fallback.returncode == 0:
        return True, f"set to {start_type} (via sc.exe)"
    return False, f"failed: {fallback.stderr.strip() or fallback.stdout.strip()}"


def backup_current_state(services: list[str]) -> dict:
    """Save current StartType of every service to BACKUP_FILE."""
    snapshot = {}
    for name in services:
        info = get_service_info(name)
        if info:
            snapshot[name] = {
                "DisplayName": info["DisplayName"],
                "Status": info["Status"],
                "StartType": info["StartType"],
            }
        else:
            snapshot[name] = {"DisplayName": None, "Status": None, "StartType": None}
    BACKUP_FILE.write_text(json.dumps({
        "timestamp": datetime.now().isoformat(),
        "services": snapshot,
    }, indent=2))
    return snapshot


def load_backup() -> dict | None:
    """Load the backup file if it exists."""
    if not BACKUP_FILE.exists():
        return None
    return json.loads(BACKUP_FILE.read_text())


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def cmd_dry_run():
    """Print what would be changed without changing anything."""
    print("=" * 72)
    print("DRY RUN — no changes will be made")
    print("=" * 72)
    for svc in SERVICES:
        info = get_service_info(svc["name"])
        status = info["Status"] if info else "NOT FOUND"
        start_type = info["StartType"] if info else "N/A"
        print(f"\n  {svc['name']}  ({svc['display']})")
        print(f"    Current : Status={status}, StartType={start_type}")
        print(f"    Would   : Stop + set Disabled")
        print(f"    Reason  : {svc['reason']}")
    print("\n" + "=" * 72)


def cmd_apply(yes: bool = False):
    """Stop and disable all services. Save a backup first."""
    if not yes:
        print("The following services will be STOPPED and DISABLED:\n")
        for svc in SERVICES:
            print(f"  • {svc['name']} ({svc['display']})")
        print(f"\nBackup will be saved to: {BACKUP_FILE}")

        try:
            resp = input("\nProceed? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            sys.exit(0)
        if resp not in ("y", "yes"):
            print("Aborted.")
            sys.exit(0)

    # Backup
    print(f"\nSaving backup to {BACKUP_FILE} ...")
    backup_current_state([s["name"] for s in SERVICES])

    # Stop + Disable
    failures = []
    for svc in SERVICES:
        name = svc["name"]
        display = svc["display"]
        print(f"\n── {name} ({display}) ──")

        ok, msg = stop_service(name)
        print(f"   Stop: {msg}")
        if not ok:
            failures.append((name, "stop", msg))

        ok, msg = set_startup_type(name, "Disabled")
        print(f"   Disable: {msg}")
        if not ok:
            failures.append((name, "disable", msg))

    print("\n" + "=" * 72)
    if failures:
        print(f"Done with {len(failures)} failure(s):")
        for f in failures:
            print(f"  - {f[0]} ({f[1]}): {f[2]}")
    else:
        print("All services stopped and disabled successfully.")
    print(f"Backup saved to: {BACKUP_FILE}")
    print("A reboot is recommended to apply changes.")


def cmd_restore():
    """Restore services from backup."""
    backup = load_backup()
    if backup is None:
        print(f"No backup found at {BACKUP_FILE}")
        sys.exit(1)

    print(f"Restoring from backup created: {backup['timestamp']}\n")
    for name, info in backup["services"].items():
        if info["StartType"] is None:
            print(f"  {name}: not found during backup, skipping")
            continue
        print(f"  {name} → {info['StartType']}  ...", end=" ")
        ok, msg = set_startup_type(name, info["StartType"])
        print(msg)

    print("\nRestore complete. A reboot is recommended.")


def cmd_list():
    """List current state of target services."""
    print(f"{'Service':<25} {'Status':<12} {'StartType':<12}")
    print("-" * 49)
    for svc in SERVICES:
        info = get_service_info(svc["name"])
        if info:
            print(f"{svc['name']:<25} {info['Status']:<12} {info['StartType']:<12}")
        else:
            print(f"{svc['name']:<25} {'N/A':<12} {'N/A':<12}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Disable problematic Windows services (black screen / slow boot)",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true",
                       help="Preview changes without applying them")
    group.add_argument("--yes", "-y", action="store_true",
                       help="Apply without confirmation prompt")
    group.add_argument("--restore", action="store_true",
                       help="Restore services from backup file")
    group.add_argument("--list", action="store_true",
                       help="List current state of target services")
    args = parser.parse_args()

    if not is_admin():
        print("ERROR: This script requires Administrator privileges.")
        print("Please right-click → 'Run as administrator', or run from an elevated terminal.")
        sys.exit(2)

    if args.restore:
        cmd_restore()
    elif args.list:
        cmd_list()
    elif args.dry_run:
        cmd_dry_run()
    else:
        cmd_apply(yes=args.yes)


if __name__ == "__main__":
    main()
