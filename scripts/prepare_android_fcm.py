import json
import os
from pathlib import Path

project = Path("buildsrc/CREDIXCEL-Control")
secret = os.environ.get("GOOGLE_SERVICES_JSON", "").strip()
if not secret:
    raise SystemExit("Falta el secreto GOOGLE_SERVICES_JSON en GitHub Actions.")

try:
    firebase_config = json.loads(secret)
except json.JSONDecodeError as exc:
    raise SystemExit("El secreto GOOGLE_SERVICES_JSON no contiene JSON válido.") from exc

if firebase_config.get("project_info", {}).get("project_id") != "credixcel-control":
    raise SystemExit("El google-services.json no pertenece al proyecto credixcel-control.")
packages = {
    client.get("client_info", {}).get("android_client_info", {}).get("package_name")
    for client in firebase_config.get("client", [])
}
if "com.credixcel.control" not in packages:
    raise SystemExit("El google-services.json no contiene el paquete com.credixcel.control.")

app_dir = project / "android" / "app"
(app_dir / "google-services.json").write_text(
    json.dumps(firebase_config, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8"
)

root_gradle = project / "build.gradle.kts"
root_text = root_gradle.read_text(encoding="utf-8")
plugin_line = '    id("com.google.gms.google-services") version "4.4.2" apply false\n'
if "com.google.gms.google-services" not in root_text:
    root_text = root_text.replace("plugins {\n", "plugins {\n" + plugin_line, 1)
root_gradle.write_text(root_text, encoding="utf-8")

app_gradle = app_dir / "build.gradle.kts"
app_text = app_gradle.read_text(encoding="utf-8")
if 'versionName = "1.2.3"' not in app_text or "versionCode = 24" not in app_text:
    raise SystemExit("La fuente base no coincide con 1.2.3 / código 24; no se modificará.")
if 'id("com.google.gms.google-services")' not in app_text:
    app_text = app_text.replace(
        '    id("org.jetbrains.kotlin.plugin.compose")\n',
        '    id("org.jetbrains.kotlin.plugin.compose")\n    id("com.google.gms.google-services")\n',
        1
    )
if 'firebase-bom' not in app_text:
    app_text = app_text.rsplit("}", 1)[0] + '''
    implementation(platform("com.google.firebase:firebase-bom:33.10.0"))
    implementation("com.google.firebase:firebase-messaging")
}
'''
app_text = app_text.replace("versionCode = 24", "versionCode = 25")
app_text = app_text.replace('versionName = "1.2.3"', 'versionName = "1.2.4"')
app_gradle.write_text(app_text, encoding="utf-8")

manifest = app_dir / "src/main/AndroidManifest.xml"
manifest_text = manifest.read_text(encoding="utf-8")
service = '''
        <service
            android:name=".fcm.CredixcelMessagingService"
            android:exported="false">
            <intent-filter>
                <action android:name="com.google.firebase.MESSAGING_EVENT" />
            </intent-filter>
        </service>
'''
if "CredixcelMessagingService" not in manifest_text:
    manifest_text = manifest_text.replace("</application>", service + "    </application>")
manifest.write_text(manifest_text, encoding="utf-8")

prefs_file = app_dir / "src/main/java/com/credixcel/control/data/Prefs.kt"
prefs_text = prefs_file.read_text(encoding="utf-8")
if "pendingRemoteCommandId" not in prefs_text:
    anchor = "    var notificationPermissionAsked: Boolean\n"
    addition = """    /** Remote command awaiting confirmation by Android. */
    var pendingRemoteCommandId: String?
        get() = p.getString("pending_remote_command_id", null)
        set(value) { p.edit().putString("pending_remote_command_id", value).commit() }

"""
    if anchor not in prefs_text:
        raise SystemExit("No se encontró el punto de inserción en Prefs.kt.")
    prefs_text = prefs_text.replace(anchor, addition + anchor, 1)
    prefs_file.write_text(prefs_text, encoding="utf-8")

main_activity = app_dir / "src/main/java/com/credixcel/control/MainActivity.kt"
main_text = main_activity.read_text(encoding="utf-8")
if "FcmTokenRegistrar.register(this, prefs)" not in main_text:
    main_text = main_text.replace(
        "        setContent { App(prefs) }",
        "        FcmTokenRegistrar.register(this, prefs)\n        setContent { App(prefs) }",
        1
    )
if "FcmTokenRegistrar.register(this@MainActivity, prefs)" not in main_text:
    main_text = main_text.replace(
        "                                    prefs.token = response.deviceToken\n",
        "                                    prefs.token = response.deviceToken\n                                    FcmTokenRegistrar.register(this@MainActivity, prefs)\n",
        1
    )
if "import com.credixcel.control.fcm.FcmTokenRegistrar" not in main_text:
    main_text = main_text.replace(
        "import com.credixcel.control.device.DeviceActions\n",
        "import com.credixcel.control.device.DeviceActions\nimport com.credixcel.control.fcm.FcmTokenRegistrar\nimport com.credixcel.control.fcm.FcmCommandAcknowledger\n",
        1
    )

old_enter = '''private fun enterLockTaskIfAuthorized(): Boolean {
    if (!DeviceActions.isDeviceOwner(this)) return false
    if (!DeviceActions.configureKiosk(this) || !DeviceActions.isLockTaskPermitted(this)) return false
    return runCatching {
        val manager = getSystemService(ActivityManager::class.java)
        if (manager.lockTaskModeState == ActivityManager.LOCK_TASK_MODE_NONE) startLockTask()
        manager.lockTaskModeState == ActivityManager.LOCK_TASK_MODE_LOCKED
    }.getOrDefault(false)
}'''
new_enter = '''private fun enterLockTaskIfAuthorized(): Boolean {
    if (!DeviceActions.isDeviceOwner(this)) return false
    if (!DeviceActions.configureKiosk(this) || !DeviceActions.isLockTaskPermitted(this)) return false
    val entered = runCatching {
        val manager = getSystemService(ActivityManager::class.java)
        if (manager.lockTaskModeState == ActivityManager.LOCK_TASK_MODE_NONE) startLockTask()
        manager.lockTaskModeState == ActivityManager.LOCK_TASK_MODE_LOCKED
    }.getOrDefault(false)
    if (entered) {
        FcmCommandAcknowledger.acknowledge(this, prefs, true, "Lock Task confirmado por Android")
    } else if (prefs.pendingRemoteCommandId != null) {
        FcmCommandAcknowledger.acknowledge(this, prefs, false, "Android no confirmó el modo Lock Task")
    }
    return entered
}'''
if old_enter not in main_text:
    raise SystemExit("No se encontró enterLockTaskIfAuthorized en MainActivity.kt.")
main_text = main_text.replace(old_enter, new_enter, 1)

main_text = main_text.replace(
    '''        if (!prefs.protectionActive) return

        if (!enterLockTaskIfAuthorized()) {''',
    '''        if (!prefs.protectionActive) {
            if (!prefs.pendingRelease && prefs.pendingRemoteCommandId != null) {
                FcmCommandAcknowledger.acknowledge(this, prefs, true, "Liberación confirmada previamente por Android")
            }
            return
        }

        if (!enterLockTaskIfAuthorized()) {''',
    1
)

main_text = main_text.replace(
    '''            prefs.pendingRelease = false
            prefs.releaseAuthorizationAt = 0L
            return "Liberación cancelada: se requiere confirmación ACTIVE + ACTIVE reciente. Se conserva la protección."''',
    '''            prefs.pendingRelease = false
            prefs.releaseAuthorizationAt = 0L
            FcmCommandAcknowledger.acknowledge(this, prefs, false, "Se requiere confirmación ACTIVE + ACTIVE reciente")
            return "Liberación cancelada: se requiere confirmación ACTIVE + ACTIVE reciente. Se conserva la protección."''',
    1
)
main_text = main_text.replace(
    '''        if (!DeviceActions.isDeviceOwner(this)) {
            return "CREDIXCEL ya no es Device Owner. Se conserva el estado local."
        }''',
    '''        if (!DeviceActions.isDeviceOwner(this)) {
            FcmCommandAcknowledger.acknowledge(this, prefs, false, "CREDIXCEL ya no es Device Owner")
            return "CREDIXCEL ya no es Device Owner. Se conserva el estado local."
        }''',
    1
)
policy_anchor = '''            prefs.pendingRelease = false
            prefs.releaseAuthorizationAt = 0L
            android.widget.Toast.makeText(
                this, "No se pudieron retirar todas las políticas. Protección restaurada: $rollback",'''
policy_replacement = '''            prefs.pendingRelease = false
            prefs.releaseAuthorizationAt = 0L
            FcmCommandAcknowledger.acknowledge(this, prefs, false, "No se pudieron retirar todas las políticas")
            android.widget.Toast.makeText(
                this, "No se pudieron retirar todas las políticas. Protección restaurada: $rollback",'''
if policy_anchor not in main_text:
    raise SystemExit("No se encontró el fallo de políticas en releaseKiosk.")
main_text = main_text.replace(policy_anchor, policy_replacement, 1)
main_text = main_text.replace(
    '''            prefs.releaseAuthorizationAt = 0L
            android.widget.Toast.makeText(
                this, "$reason. Protección restaurada: $rollback",''',
    '''            prefs.releaseAuthorizationAt = 0L
            FcmCommandAcknowledger.acknowledge(this, prefs, false, reason)
            android.widget.Toast.makeText(
                this, "$reason. Protección restaurada: $rollback",''',
    1
)
main_text = main_text.replace(
    '''                        prefs.releaseAuthorizationAt = 0L
                        releaseInProgress = false
                        finish()''',
    '''                        prefs.releaseAuthorizationAt = 0L
                        releaseInProgress = false
                        FcmCommandAcknowledger.acknowledge(this, prefs, true, "Liberación confirmada por Android")
                        finish()''',
    1
)

main_activity.write_text(main_text, encoding="utf-8")

fcm_dir = app_dir / "src/main/java/com/credixcel/control/fcm"
fcm_dir.mkdir(parents=True, exist_ok=True)

(fcm_dir / "FcmTokenRegistrar.kt").write_text(r'''package com.credixcel.control.fcm

import android.content.Context
import com.credixcel.control.data.Prefs
import com.google.firebase.messaging.FirebaseMessaging
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

object FcmTokenRegistrar {
    fun register(context: Context, prefs: Prefs) {
        FirebaseMessaging.getInstance().token.addOnSuccessListener { token ->
            Thread { postToken(context.applicationContext, prefs, token) }.start()
        }
    }

    fun onNewToken(context: Context, token: String) {
        val prefs = Prefs(context)
        Thread { postToken(context.applicationContext, prefs, token) }.start()
    }

    private fun postToken(context: Context, prefs: Prefs, fcmToken: String) {
        val deviceId = prefs.deviceId ?: return
        val deviceToken = prefs.token ?: return
        val base = prefs.serverUrl.trimEnd('/')
        if (!base.startsWith("https://")) return
        var connection: HttpURLConnection? = null
        try {
            connection = (URL("$base/api/device/fcm-token").openConnection() as HttpURLConnection).apply {
                requestMethod = "POST"
                connectTimeout = 15000
                readTimeout = 15000
                doOutput = true
                setRequestProperty("Content-Type", "application/json")
            }
            val body = JSONObject()
                .put("deviceId", deviceId)
                .put("deviceToken", deviceToken)
                .put("fcmToken", fcmToken)
                .toString()
            connection.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            connection.responseCode
        } catch (_: Exception) {
            // Token registration is retried the next time the app starts.
        } finally {
            connection?.disconnect()
        }
    }
}
''', encoding="utf-8")

(fcm_dir / "CredixcelMessagingService.kt").write_text(r'''package com.credixcel.control.fcm

import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage

class CredixcelMessagingService : FirebaseMessagingService() {
    override fun onNewToken(token: String) {
        FcmTokenRegistrar.onNewToken(this, token)
    }

    override fun onMessageReceived(message: RemoteMessage) {
        FcmCommandProcessor.process(this, message.data)
    }
}
''', encoding="utf-8")

(fcm_dir / "FcmCommandProcessor.kt").write_text(r'''package com.credixcel.control.fcm

import android.content.Context
import com.credixcel.control.data.Prefs
import com.credixcel.control.device.DeviceActions
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

object FcmCommandProcessor {
    fun process(context: Context, data: Map<String, String>) {
        val prefs = Prefs(context)
        val deviceId = prefs.deviceId ?: return
        val deviceToken = prefs.token ?: return
        val commandId = data["commandId"] ?: return
        if (data["deviceId"] != deviceId) return
        val base = prefs.serverUrl.trimEnd('/')
        if (!base.startsWith("https://")) return

        try {
            val claim = post("$base/api/device/commands/claim", JSONObject()
                .put("deviceId", deviceId)
                .put("deviceToken", deviceToken)
                .put("commandId", commandId))
            val action = claim.optString("action")
            prefs.pendingRemoteCommandId = commandId

            when (action) {
                "LOCK_DEVICE" -> {
                    prefs.pendingRelease = false
                    prefs.releaseAuthorizationAt = 0L
                    prefs.lastDeviceStatus = "LOCKED"
                    prefs.protectionActive = true
                    val policies = DeviceActions.applyFinancingProtection(context)
                    val kiosk = DeviceActions.configureKiosk(context) && DeviceActions.isLockTaskPermitted(context)
                    if (!policies || !kiosk) {
                        FcmCommandAcknowledger.acknowledge(context, prefs, false, "Android no confirmó políticas o kiosco")
                        return
                    }
                    prefs.lockCommandReady = true
                    // Best-effort screen lock; the foreground Activity's Lock Task confirmation is authoritative.
                    DeviceActions.lock(context)
                    // MainActivity acknowledges only after Android confirms Lock Task.
                }
                "UNLOCK_DEVICE" -> {
                    if (data["deviceStatus"] != "ACTIVE" || data["contractStatus"] != "ACTIVE") {
                        FcmCommandAcknowledger.acknowledge(context, prefs, false, "La liberación requiere ACTIVE + ACTIVE")
                        return
                    }
                    prefs.lastDeviceStatus = "ACTIVE"
                    prefs.lastContractStatus = "ACTIVE"
                    prefs.releaseAuthorizationAt = System.currentTimeMillis()
                    prefs.pendingRelease = true
                    // MainActivity acknowledges only after Lock Task exit and HOME handoff.
                }
                else -> FcmCommandAcknowledger.acknowledge(context, prefs, false, "Acción remota desconocida")
            }
        } catch (error: Exception) {
            FcmCommandAcknowledger.acknowledge(context, prefs, false, error.javaClass.simpleName)
        }
    }

    private fun post(url: String, body: JSONObject): JSONObject {
        val connection = (URL(url).openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            connectTimeout = 15000
            readTimeout = 15000
            doOutput = true
            setRequestProperty("Content-Type", "application/json")
        }
        try {
            connection.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            val code = connection.responseCode
            val stream = if (code in 200..299) connection.inputStream else connection.errorStream
            val response = stream?.bufferedReader()?.use { it.readText() } ?: "{}"
            if (code !in 200..299) throw IllegalStateException("El servidor rechazó la orden")
            return JSONObject(response)
        } finally {
            connection.disconnect()
        }
    }
}
''', encoding="utf-8")

(fcm_dir / "FcmCommandAcknowledger.kt").write_text(r'''package com.credixcel.control.fcm

import android.content.Context
import com.credixcel.control.data.Prefs
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

object FcmCommandAcknowledger {
    fun acknowledge(context: Context, prefs: Prefs, success: Boolean, result: String) {
        val commandId = prefs.pendingRemoteCommandId ?: return
        val deviceId = prefs.deviceId ?: return
        val deviceToken = prefs.token ?: return
        val base = prefs.serverUrl.trimEnd('/')
        if (!base.startsWith("https://")) return
        Thread {
            var connection: HttpURLConnection? = null
            try {
                connection = (URL("$base/api/device/commands/ack").openConnection() as HttpURLConnection).apply {
                    requestMethod = "POST"
                    connectTimeout = 15000
                    readTimeout = 15000
                    doOutput = true
                    setRequestProperty("Content-Type", "application/json")
                }
                val body = JSONObject()
                    .put("deviceId", deviceId)
                    .put("deviceToken", deviceToken)
                    .put("commandId", commandId)
                    .put("success", success)
                    .put("result", result.take(500))
                    .toString()
                connection.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
                if (connection.responseCode in 200..299 && prefs.pendingRemoteCommandId == commandId) {
                    prefs.pendingRemoteCommandId = null
                }
            } catch (_: Exception) {
                // Keep the pending ID so the device can retry its acknowledgement later.
            } finally {
                connection?.disconnect()
            }
        }.start()
    }
}
''', encoding="utf-8")

checks = {
    "versión 1.2.4": 'versionName = "1.2.4"' in app_gradle.read_text(encoding="utf-8") and "versionCode = 25" in app_gradle.read_text(encoding="utf-8"),
    "servicio Firebase declarado": "CredixcelMessagingService" in manifest.read_text(encoding="utf-8"),
    "registro FCM tras enrolamiento": "FcmTokenRegistrar.register(this@MainActivity, prefs)" in main_activity.read_text(encoding="utf-8"),
    "bloqueo confirmado por Lock Task": "Lock Task confirmado por Android" in main_activity.read_text(encoding="utf-8"),
    "liberación confirmada por Android": "Liberación confirmada por Android" in main_activity.read_text(encoding="utf-8"),
    "procesador y confirmador FCM": (fcm_dir / "FcmCommandProcessor.kt").is_file() and (fcm_dir / "FcmCommandAcknowledger.kt").is_file(),
}
for label, ok in checks.items():
    print(("OK: " if ok else "ERROR: ") + label)
if not all(checks.values()):
    raise SystemExit("La integración FCM no superó las verificaciones.")
print("Integración FCM preparada; versión 1.2.4 y órdenes remotas LOCK/UNLOCK integradas.")
