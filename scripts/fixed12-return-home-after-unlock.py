#!/usr/bin/env python3
"""Return to the external Android launcher only after authenticated UNLOCK ACK."""
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
main_path = root / "android/app/src/main/java/com/credixcel/control/MainActivity.kt"
if not main_path.is_file():
    raise SystemExit("Missing generated MainActivity.kt")

source = main_path.read_text(encoding="utf-8")

ack_line = "                if (acknowledged) prefs.clearPendingCommand()"
replacement = """                if (acknowledged) {
                    prefs.clearPendingCommand()
                    if (action == "UNLOCK_DEVICE" && success) {
                        returnToSystemHome()
                    }
                }"""
if ack_line in source:
    source = source.replace(ack_line, replacement, 1)
elif "if (action == \"UNLOCK_DEVICE\" && success) {\n                        returnToSystemHome()" not in source:
    raise SystemExit("Could not find the authenticated ACK completion point")

helper = """    /**
     * Leave CREDIXCEL only after the server has accepted the successful UNLOCK ACK.
     * Resolve an Android HOME activity outside this package so this app's own HOME
     * intent filter cannot relaunch MainActivity.
     */
    private fun returnToSystemHome(): Boolean {
        val homeIntent = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME)
        val externalHome = packageManager.queryIntentActivities(
            homeIntent,
            android.content.pm.PackageManager.MATCH_DEFAULT_ONLY
        ).firstOrNull { it.activityInfo.packageName != packageName } ?: return false

        homeIntent.component = android.content.ComponentName(
            externalHome.activityInfo.packageName,
            externalHome.activityInfo.name
        )
        homeIntent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED)

        return runCatching {
            getSystemService(android.app.NotificationManager::class.java).cancel(1207)
            startActivity(homeIntent)
            finish()
            true
        }.getOrDefault(false)
    }

"""
anchor = "    @Composable\n    private fun AdminLockScreen()"
if "private fun returnToSystemHome(): Boolean" not in source:
    if anchor not in source:
        raise SystemExit("Could not find insertion point before AdminLockScreen")
    source = source.replace(anchor, helper + anchor, 1)

if "if (action == \"UNLOCK_DEVICE\" && success)" not in source:
    raise SystemExit("UNLOCK home transition was not inserted")
if "PackageManager.MATCH_DEFAULT_ONLY" not in source:
    raise SystemExit("External HOME resolution guard was not inserted")

main_path.write_text(source, encoding="utf-8")
print("Authenticated UNLOCK now returns to an external Android HOME activity after ACK.")
