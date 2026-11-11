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
app_text = app_text.replace("versionCode = 2", "versionCode = 3")
app_text = app_text.replace('versionName = "1.0.1"', 'versionName = "1.0.2"')
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
        "import com.credixcel.control.device.DeviceActions\nimport com.credixcel.control.fcm.FcmTokenRegistrar\n",
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

        var success = false
        var result = "Orden no compatible"
        try {
            val claim = post("$base/api/device/commands/claim", JSONObject()
                .put("deviceId", deviceId)
                .put("deviceToken", deviceToken)
                .put("commandId", commandId))
            if (claim.optString("action") == "LOCK_DEVICE") {
                success = DeviceActions.lock(context)
                result = if (success) "Bloqueo de pantalla ejecutado" else "El dispositivo no tiene privilegios Device Owner"
            }
        } catch (error: Exception) {
            result = error.javaClass.simpleName
        }

        try {
            post("$base/api/device/commands/ack", JSONObject()
                .put("deviceId", deviceId)
                .put("deviceToken", deviceToken)
                .put("commandId", commandId)
                .put("success", success)
                .put("result", result))
        } catch (_: Exception) {
            // The server keeps the command unconfirmed if acknowledgement cannot be delivered.
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
            val stream = if (connection.responseCode in 200..299) connection.inputStream else connection.errorStream
            val response = stream?.bufferedReader()?.use { it.readText() } ?: "{}"
            if (connection.responseCode !in 200..299) {
                throw IllegalStateException("El servidor rechazó la orden")
            }
            return JSONObject(response)
        } finally {
            connection.disconnect()
        }
    }
}
''', encoding="utf-8")

print("Integración FCM preparada; google-services.json validado y archivos Android actualizados.")
