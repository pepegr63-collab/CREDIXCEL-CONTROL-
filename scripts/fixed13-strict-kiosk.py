#!/usr/bin/env python3
"""Apply strict Lock Task system-UI policy for CREDIXCEL 1.3.3."""
from pathlib import Path
import re
import sys

root = Path(sys.argv[1]).resolve()
device_path = root / "android/app/src/main/java/com/credixcel/control/device/DeviceActions.kt"
if not device_path.is_file():
    raise SystemExit("Missing generated DeviceActions.kt")

source = device_path.read_text(encoding="utf-8")
pattern = re.compile(
    r"manager\\.setLockTaskFeatures\\(\\s*"
    r"admin\\(context\\),\\s*"
    r"DevicePolicyManager\\.LOCK_TASK_FEATURE_HOME\\s+or\\s+"
    r"DevicePolicyManager\\.LOCK_TASK_FEATURE_GLOBAL_ACTIONS\\s+or\\s+"
    r"DevicePolicyManager\\.LOCK_TASK_FEATURE_KEYGUARD\\s+or\\s+"
    r"DevicePolicyManager\\.LOCK_TASK_FEATURE_NOTIFICATIONS\\s*"
    r"\\)",
    re.MULTILINE,
)
replacement = "manager.setLockTaskFeatures(admin(context), 0)"
source, count = pattern.subn(replacement, source, count=1)
if count != 1:
    if "manager.setLockTaskFeatures(admin(context), 0)" not in source:
        raise SystemExit("Expected 1.3.2 Lock Task feature block not found; refusing unsafe partial patch")
if "LOCK_TASK_FEATURE_NOTIFICATIONS" in source:
    raise SystemExit("Notification shade remains enabled in Lock Task features")
if "manager.setLockTaskFeatures(admin(context), 0)" not in source:
    raise SystemExit("Strict Lock Task feature flags were not applied")
device_path.write_text(source, encoding="utf-8")
print("OK: Lock Task optional system UI features disabled (flags=0).")
