#!/usr/bin/env python3
"""Correct authenticated UNLOCK cleanup and HOME selection for CREDIXCEL 1.3.6."""
from pathlib import Path
import re
import sys

root = Path(sys.argv[1]).resolve()
base = root / "android/app/src/main/java/com/credixcel/control"
device_path = base / "device/DeviceActions.kt"
main_path = base / "MainActivity.kt"
build_path = root / "android/app/build.gradle.kts"
for path in (device_path, main_path, build_path):
    if not path.is_file():
        raise SystemExit(f"Missing expected generated source: {path}")

device = device_path.read_text(encoding="utf-8")
start = device.find("    fun clearKioskConfiguration(context: Context): Boolean {")
end = device.find("\n    fun applyUninstallPolicy", start)
if start < 0 or end < 0:
    raise SystemExit("Cannot locate clearKioskConfiguration()")
clear_fn = '''    /**
     * Remove only CREDIXCEL-owned kiosk state. HOME resolution is verified separately:
     * Android 15 can temporarily resolve HOME to Settings.FallbackHome during setup.
     */
    fun clearKioskConfiguration(context: Context): Boolean {
        if (!isDeviceOwner(context)) return false
        return try {
            val manager = dpm(context)
            manager.clearPackagePersistentPreferredActivities(admin(context), context.packageName)
            manager.setLockTaskPackages(admin(context), emptyArray<String>())
            !manager.isLockTaskPermitted(context.packageName)
        } catch (_: SecurityException) {
            false
        } catch (_: IllegalStateException) {
            false
        } catch (_: IllegalArgumentException) {
            false
        }
    }
'''
device = device[:start] + clear_fn + device[end:]
device_path.write_text(device, encoding="utf-8")

main = main_path.read_text(encoding="utf-8")
# Exclude transient Android setup/fallback handlers; do not make the DPC cleanup depend on HOME resolution.
old_filter = '''                info.packageName != packageName &&
                    !info.name.endsWith(".ResolverActivity") &&
                    runCatching {
'''
new_filter = '''                info.packageName != packageName &&
                    !info.name.endsWith(".ResolverActivity") &&
                    !info.name.contains("FallbackHome", ignoreCase = true) &&
                    runCatching {
'''
if old_filter not in main:
    raise SystemExit("Expected HOME candidate filter not found in MainActivity")
main = main.replace(old_filter, new_filter, 1)

# Make the failure reason reflect the actual remaining state.
old_unlock = '''            val kioskCleared = DeviceActions.clearKioskConfiguration(this)
            if (!kioskCleared || DeviceActions.isLockTaskPermitted(this)) {
                prefs.protectionActive = true
                lockScreenVisible = true
                return "UNLOCK_FAILED_KIOSK_CONFIGURATION_NOT_CLEARED"
            }
'''
new_unlock = '''            val kioskCleared = DeviceActions.clearKioskConfiguration(this)
            if (!kioskCleared) {
                prefs.protectionActive = true
                lockScreenVisible = true
                return if (DeviceActions.isLockTaskPermitted(this)) {
                    "UNLOCK_FAILED_LOCK_TASK_ALLOWLIST_NOT_CLEARED"
                } else {
                    "UNLOCK_FAILED_KIOSK_POLICY_CLEANUP"
                }
            }
'''
if old_unlock not in main:
    raise SystemExit("Expected releaseKiosk cleanup guard not found")
main = main.replace(old_unlock, new_unlock, 1)

# On success, the server ACK remains the gate for leaving CREDIXCEL; HOME launch is attempted afterward.
# Return-to-HOME must not select the temporary Settings.FallbackHome activity.
if new_filter not in main:
    raise SystemExit("FallbackHome exclusion was not retained")
main_path.write_text(main, encoding="utf-8")

build = build_path.read_text(encoding="utf-8")
if 'versionName = "1.3.5"' not in build or "versionCode = 36" not in build:
    raise SystemExit("Expected 1.3.5 / versionCode 36 before applying 1.3.6 patch")
build = build.replace("versionCode = 36", "versionCode = 37", 1)
build = build.replace('versionName = "1.3.5"', 'versionName = "1.3.6"', 1)
build_path.write_text(build, encoding="utf-8")

checks = {
    "clear only this DPC's persistent HOME preference": "clearPackagePersistentPreferredActivities(admin(context), context.packageName)" in device,
    "clear Lock Task allowlist": "setLockTaskPackages(admin(context), emptyArray<String>())" in device,
    "verify allowlist independently of HOME resolver": "!manager.isLockTaskPermitted(context.packageName)" in device and "resolveActivity(" not in clear_fn,
    "exclude transient FallbackHome from HOME candidate selection": 'contains("FallbackHome", ignoreCase = true)' in main,
    "specific allowlist failure diagnostic": "UNLOCK_FAILED_LOCK_TASK_ALLOWLIST_NOT_CLEARED" in main,
    "preserve protection on failed cleanup": 'prefs.protectionActive = true' in new_unlock,
    "version 1.3.6 / code 37": 'versionName = "1.3.6"' in build and "versionCode = 37" in build,
}
for label, ok in checks.items():
    print(("PASS: " if ok else "FAIL: ") + label)
if not all(checks.values()):
    raise SystemExit("CREDIXCEL 1.3.6 unlock validation failed")
print("CREDIXCEL 1.3.6 verified-unlock patch applied.")
