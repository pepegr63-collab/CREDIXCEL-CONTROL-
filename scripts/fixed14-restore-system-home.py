#!/usr/bin/env python3
"""Make Android's system launcher the persistent HOME after authenticated CREDIXCEL unlock."""
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
device_path = root / "android/app/src/main/java/com/credixcel/control/device/DeviceActions.kt"
main_path = root / "android/app/src/main/java/com/credixcel/control/MainActivity.kt"
build_path = root / "android/app/build.gradle.kts"
for p in (device_path, main_path, build_path):
    if not p.is_file():
        raise SystemExit(f"Missing required file: {p}")

device = device_path.read_text(encoding="utf-8")
start = device.find("    fun clearKioskConfiguration(context: Context): Boolean {")
end = device.find("\n    fun applyUninstallPolicy", start)
if start < 0 or end < 0:
    raise SystemExit("Cannot locate clearKioskConfiguration() boundaries")

replacement = '''    /**
     * Replace CREDIXCEL's persistent HOME with the installed system launcher.
     * Clearing our preference alone can leave Android resolving HOME back to this DPC.
     */
    fun clearKioskConfiguration(context: Context): Boolean {
        if (!isDeviceOwner(context)) return false
        return try {
            val manager = dpm(context)
            val homeIntent = Intent(Intent.ACTION_MAIN).apply {
                addCategory(Intent.CATEGORY_HOME)
                addCategory(Intent.CATEGORY_DEFAULT)
            }
            val launcher = context.packageManager.queryIntentActivities(
                homeIntent,
                android.content.pm.PackageManager.MATCH_DEFAULT_ONLY
            ).asSequence()
                .map { it.activityInfo }
                .filter { info ->
                    info.packageName != context.packageName &&
                        !info.name.endsWith(".ResolverActivity") &&
                        runCatching {
                            val app = context.packageManager.getApplicationInfo(info.packageName, 0)
                            (app.flags and android.content.pm.ApplicationInfo.FLAG_SYSTEM) != 0
                        }.getOrDefault(false)
                }
                .sortedWith(
                    compareByDescending<android.content.pm.ActivityInfo> { it.priority }
                        .thenBy { it.packageName }
                )
                .firstOrNull() ?: return false

            val filter = IntentFilter(Intent.ACTION_MAIN).apply {
                addCategory(Intent.CATEGORY_HOME)
                addCategory(Intent.CATEGORY_DEFAULT)
            }

            manager.clearPackagePersistentPreferredActivities(admin(context), context.packageName)
            manager.addPersistentPreferredActivity(
                admin(context),
                filter,
                ComponentName(launcher.packageName, launcher.name)
            )
            manager.setLockTaskPackages(admin(context), emptyArray<String>())

            val resolved = context.packageManager.resolveActivity(
                homeIntent,
                android.content.pm.PackageManager.MATCH_DEFAULT_ONLY
            )?.activityInfo

            !manager.isLockTaskPermitted(context.packageName) &&
                resolved != null &&
                resolved.packageName == launcher.packageName &&
                resolved.name == launcher.name &&
                resolved.packageName != context.packageName
        } catch (_: SecurityException) {
            false
        } catch (_: IllegalStateException) {
            false
        } catch (_: IllegalArgumentException) {
            false
        }
    }
'''
device = device[:start] + replacement + device[end:]
device_path.write_text(device, encoding="utf-8")

main = main_path.read_text(encoding="utf-8")
start = main.find("    private fun returnToSystemHome(): Boolean {")
end = main.find("\n    @Composable\n    private fun AdminLockScreen()", start)
if start < 0 or end < 0:
    raise SystemExit("Cannot locate returnToSystemHome() boundaries")
helper = '''    private fun returnToSystemHome(): Boolean {
        val homeIntent = Intent(Intent.ACTION_MAIN).apply {
            addCategory(Intent.CATEGORY_HOME)
            addCategory(Intent.CATEGORY_DEFAULT)
        }
        val resolved = packageManager.resolveActivity(
            homeIntent,
            android.content.pm.PackageManager.MATCH_DEFAULT_ONLY
        )?.activityInfo ?: return false

        // Never accept CREDIXCEL itself or Android's chooser as the system HOME.
        if (resolved.packageName == packageName ||
            resolved.name.endsWith(".ResolverActivity")) return false

        homeIntent.component = android.content.ComponentName(resolved.packageName, resolved.name)
        homeIntent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED)

        return runCatching {
            getSystemService(android.app.NotificationManager::class.java).cancel(1207)
            startActivity(homeIntent)
            finish()
            true
        }.getOrDefault(false)
    }
'''
main = main[:start] + helper + main[end:]
main_path.write_text(main, encoding="utf-8")

build = build_path.read_text(encoding="utf-8")
if 'versionName = "1.3.3"' not in build or "versionCode = 34" not in build:
    raise SystemExit("Expected exact 1.3.3 / versionCode 34 source before bump")
build = build.replace("versionCode = 34", "versionCode = 35", 1)
build = build.replace('versionName = "1.3.3"', 'versionName = "1.3.4"', 1)
build_path.write_text(build, encoding="utf-8")

checks = {
    "system launcher selected from installed system HOME activities": "FLAG_SYSTEM" in device and "queryIntentActivities" in device,
    "persistent HOME reassigned to external launcher": "addPersistentPreferredActivity(" in device and "ComponentName(launcher.packageName, launcher.name)" in device,
    "HOME resolution verified after reassignment": "resolved.packageName == launcher.packageName" in device and "resolved.packageName != context.packageName" in device,
    "Lock Task allowlist cleared": "setLockTaskPackages(admin(context), emptyArray<String>())" in device,
    "MainActivity refuses self/Resolver HOME": 'resolved.packageName == packageName' in main and 'resolved.name.endsWith(".ResolverActivity")' in main,
    "version 1.3.4 / code 35": 'versionName = "1.3.4"' in build and "versionCode = 35" in build,
}
for label, ok in checks.items():
    print(("OK: " if ok else "ERROR: ") + label)
if not all(checks.values()):
    raise SystemExit("Post-patch validation failed")
print("CREDIXCEL 1.3.4 HOME restoration patch applied.")
