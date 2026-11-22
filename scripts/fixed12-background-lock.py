#!/usr/bin/env python3
"""Best-effort background command launch for Android 9+ managed devices.

The OS may still block background activity launches (including on Android 15).
When blocked, the authenticated command remains pending and the notification is
kept as a user-visible fallback. This patch does not use hidden permissions,
lockNow(), or credential manipulation.
"""
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
service_path = root / "android/app/src/main/java/com/credixcel/control/messaging/CredixcelMessagingService.kt"
manifest_path = root / "android/app/src/main/AndroidManifest.xml"

if not service_path.is_file() or not manifest_path.is_file():
    raise SystemExit("Missing generated messaging service or AndroidManifest.xml")

s = service_path.read_text(encoding="utf-8")

imports = [
    ("import android.app.ActivityOptions\n", "import android.app.PendingIntent\n"),
    ("import android.util.Log\n", "import android.os.Build\n"),
    ("import com.credixcel.control.device.DeviceActions\n", "import com.credixcel.control.data.Prefs\n"),
]
for new_import, anchor in imports:
    if new_import.strip() not in s:
        if anchor not in s:
            raise SystemExit(f"Missing import anchor: {anchor.strip()}")
        s = s.replace(anchor, anchor + new_import, 1)

old = '''                            prefs.savePendingCommand(commandId, claimed.action)
                            sendBroadcast(Intent(ACTION_CREDIXCEL_COMMAND_PENDING).setPackage(packageName))
                            showCommandNotification(claimed.action)'''
new = '''                            prefs.savePendingCommand(commandId, claimed.action)
                            // If Android permits a background launch, bring MainActivity directly
                            // into Lock Task. Android 10+ / 15 may block this launch; never bypass
                            // BAL restrictions. Keep the notification as a safe fallback.
                            val launched = launchCommandActivity(claimed.action, prefs)
                            sendBroadcast(Intent(ACTION_CREDIXCEL_COMMAND_PENDING).setPackage(packageName))
                            showCommandNotification(claimed.action, launched)'''
if old in s:
    s = s.replace(old, new, 1)
elif "launchCommandActivity(claimed.action, prefs)" not in s:
    raise SystemExit("Could not find authenticated pending-command dispatch block")

old_notify = '''    private fun showCommandNotification(action: String) {'''
new_notify = '''    private fun launchCommandActivity(action: String, prefs: Prefs): Boolean {
        if (action == "LOCK_DEVICE") {
            // Configure Device Owner policy before requesting launch directly into Lock Task.
            val ready = DeviceActions.isDeviceOwner(this) &&
                DeviceActions.configureKiosk(this) &&
                DeviceActions.isLockTaskPermitted(this) &&
                DeviceActions.applyFinancingProtection(this)
            if (!ready) return false
            prefs.protectionActive = true
        }

        val intent = Intent(this, MainActivity::class.java).addFlags(
            Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP
        )
        return try {
            val options = ActivityOptions.makeBasic()
            if (action == "LOCK_DEVICE" && Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                options.setLockTaskEnabled(true)
            }
            startActivity(intent, options.toBundle())
            true
        } catch (e: Exception) {
            Log.w("CredixcelFCM", "Android did not allow automatic command activity launch", e)
            false
        }
    }

    private fun showCommandNotification(action: String, automaticLaunchRequested: Boolean) {'''
if old_notify in s:
    s = s.replace(old_notify, new_notify, 1)
elif "private fun showCommandNotification(action: String, automaticLaunchRequested: Boolean)" not in s:
    raise SystemExit("Could not find notification function signature")

old_text = '''            .setContentText("Orden pendiente: $label. Toca para procesarla.")
            .setStyle(NotificationCompat.BigTextStyle().bigText("CREDIXCEL recibió una orden administrativa. Abre esta notificación para aplicar y confirmar el resultado."))'''
new_text = '''            .setContentText(if (automaticLaunchRequested) "Orden recibida. Si la pantalla no cambia, toca para procesarla." else "Orden pendiente: $label. Toca para procesarla.")
            .setStyle(NotificationCompat.BigTextStyle().bigText("CREDIXCEL recibió una orden administrativa. Android puede impedir que una app en segundo plano abra su pantalla; si no se activa automáticamente, toca esta notificación para continuar."))'''
if old_text in s:
    s = s.replace(old_text, new_text, 1)
elif "Android puede impedir que una app en segundo plano" not in s:
    raise SystemExit("Could not find notification content block")

service_path.write_text(s, encoding="utf-8")

m = manifest_path.read_text(encoding="utf-8")
activity_marker = '''            android:launchMode="singleTask">'''
if 'android:lockTaskMode="if_whitelisted"' not in m:
    if activity_marker not in m:
        raise SystemExit("Could not find MainActivity launchMode in manifest")
    m = m.replace(activity_marker, '''            android:launchMode="singleTask"
            android:lockTaskMode="if_whitelisted">''', 1)
manifest_path.write_text(m, encoding="utf-8")

print("Applied best-effort background command launch; notification fallback retained.")
