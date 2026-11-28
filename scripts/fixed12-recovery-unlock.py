#!/usr/bin/env python3
"""Static safety gate for CREDIXCEL authenticated recovery UNLOCK, release 1.3.5."""
from pathlib import Path
import sys

root = Path(sys.argv[1])
base = root / "android/app/src/main"
api = (base / "java/com/credixcel/control/network/Api.kt").read_text()
service = (base / "java/com/credixcel/control/messaging/CredixcelMessagingService.kt").read_text()
activity = (base / "java/com/credixcel/control/MainActivity.kt").read_text()
device = (base / "java/com/credixcel/control/device/DeviceActions.kt").read_text()
gradle_lines = [line.strip() for line in (root / "android/app/build.gradle.kts").read_text().splitlines()]
release_start = activity.index("private suspend fun releaseKiosk()")
release_end = activity.index("\n    @Composable", release_start)
release_body = activity[release_start:release_end]

checks = {
    "authenticated command claim endpoint": '/api/device/commands/claim' in api,
    "authenticated command ACK endpoint": '/api/device/commands/ack' in api,
    "FCM stores the pending authenticated command": 'savePendingCommand(commandId, claimed.action)' in service,
    "FCM keeps notification fallback": 'showCommandNotification(claimed.action, launched)' in service,
    "pending UNLOCK is executed by foreground Activity": '"UNLOCK_DEVICE" ->' in activity and 'releaseKiosk()' in activity,
    "Lock Task exit is verified": "lockTaskModeState" in activity and "ActivityManager.LOCK_TASK_MODE_NONE" in activity,
    "kiosk configuration is removed in release path": 'DeviceActions.clearKioskConfiguration(this)' in release_body,
    "financing policies are released in release path": 'DeviceActions.releaseFinancingProtection(this)' in release_body,
    "release clears HOME and allowlist before stopping Lock Task": (
        release_body.find("DeviceActions.clearKioskConfiguration(this)") >= 0
        and release_body.find("DeviceActions.clearKioskConfiguration(this)") < release_body.find("stopLockTask()")
    ),
    "release waits for Android to exit all Lock Task tasks": (
        "for (attempt in 0 until 40)" in release_body
        and "ActivityManager.LOCK_TASK_MODE_NONE" in release_body
    ),
    "financing restrictions are removed only after Lock Task exit": (
        release_body.find("ActivityManager.LOCK_TASK_MODE_NONE") < release_body.find("DeviceActions.releaseFinancingProtection(this)")
    ),
    "failed release preserves protection flag": "prefs.protectionActive = true" in release_body and "UNLOCK_FAILED" in release_body,
    "allowlist removal is verified against DevicePolicyManager": "!manager.isLockTaskPermitted(context.packageName)" in device,
    "ACK occurs after execution result": 'acknowledgeCommand(CommandAckRequest(id, deviceToken, commandId, success, result))' in activity,
    "strict Lock Task flags disable optional system UI": "manager.setLockTaskFeatures(admin(context), 0)" in device,
    "notification shade is disabled in Lock Task": "LOCK_TASK_FEATURE_NOTIFICATIONS" not in device,
    "UNLOCK returns HOME only after successful ACK": (
        'if (acknowledged)' in activity
        and 'if (action == "UNLOCK_DEVICE" && success)' in activity
        and activity.find("if (acknowledged)") < activity.find("returnToSystemHome()")
    ),
    "HOME selection excludes CREDIXCEL and ResolverActivity": (
        "queryIntentActivities" in activity
        and "info.packageName != packageName" in activity
        and '!info.name.endsWith(".ResolverActivity")' in activity
    ),
    "HOME return explicitly launches selected system launcher": (
        "component = android.content.ComponentName(launcher.packageName, launcher.name)" in activity
        and "startActivity(explicitHome)" in activity
        and "finish()" in activity
    ),
    "release versionCode 37": "versionCode = 37" in gradle_lines,
    "release versionName 1.3.6": 'versionName = "1.3.6"' in gradle_lines,
}
failed = [name for name, ok in checks.items() if not ok]
for name, ok in checks.items():
    print(("PASS" if ok else "FAIL") + ": " + name)
if failed:
    raise SystemExit("Recovery UNLOCK static validation failed: " + ", ".join(failed))
print("Authenticated recovery UNLOCK static validation passed.")
