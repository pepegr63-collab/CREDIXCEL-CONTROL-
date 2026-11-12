import pathlib, re, sys

root = pathlib.Path(sys.argv[1])
app = root / "android/app/src/main"
manifest = app / "AndroidManifest.xml"
main = app / "java/com/credixcel/control/MainActivity.kt"
device = app / "java/com/credixcel/control/device/DeviceActions.kt"
build = root / "android/app/build.gradle.kts"

for p in (manifest, main, device, build):
    if not p.is_file():
        raise SystemExit(f"No se encontró archivo requerido: {p}")

# Keep LAUNCHER and add HOME/DEFAULT for Android's managed HOME selection.
m = manifest.read_text()
pat = r'(<activity\\s+android:name="\\.MainActivity"[^>]*>)(.*?)(</activity>)'
match = re.search(pat, m, re.S)
if not match:
    raise SystemExit("No se encontró la declaración de MainActivity en el manifiesto")
body = match.group(2)
if 'android.intent.category.HOME' not in body:
    body += '''\\n            <intent-filter>\\n                <action android:name="android.intent.action.MAIN" />\\n                <category android:name="android.intent.category.HOME" />\\n                <category android:name="android.intent.category.DEFAULT" />\\n            </intent-filter>'''
    m = m[:match.start(2)] + body + m[match.end(2):]
manifest.write_text(m)

# Configure/clear persistent HOME together with Lock Task, including from WorkManager.
d = device.read_text()
if 'fun updateManagedLockAllowlist(' not in d:
    helper = '''\\n    /** Configura Lock Task y HOME persistente desde Activity o Worker. */\\n    fun updateManagedLockAllowlist(context: Context, locked: Boolean): Boolean {\\n        if (!isDeviceOwner(context)) return false\\n        return try {\\n            val manager = dpm(context)\\n            if (locked) {\\n                manager.setLockTaskPackages(admin(context), arrayOf(context.packageName))\\n                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {\\n                    manager.setLockTaskFeatures(\\n                        admin(context),\\n                        DevicePolicyManager.LOCK_TASK_FEATURE_GLOBAL_ACTIONS or\\n                            DevicePolicyManager.LOCK_TASK_FEATURE_KEYGUARD\\n                    )\\n                }\\n                val home = android.content.IntentFilter(android.content.Intent.ACTION_MAIN).apply {\\n                    addCategory(android.content.Intent.CATEGORY_HOME)\\n                    addCategory(android.content.Intent.CATEGORY_DEFAULT)\\n                }\\n                manager.addPersistentPreferredActivity(\\n                    admin(context), home,\\n                    ComponentName(context, com.credixcel.control.MainActivity::class.java)\\n                )\\n            } else {\\n                manager.clearPackagePersistentPreferredActivities(admin(context), context.packageName)\\n                manager.setLockTaskPackages(admin(context), emptyArray())\\n            }\\n            true\\n        } catch (_: Exception) { false }\\n    }\\n\\n'''
    needle = '    fun lock(context: Context): Boolean {'
    if needle not in d:
        raise SystemExit("No se encontró el punto de inserción en DeviceActions.kt")
    d = d.replace(needle, helper + needle, 1)

old_enter = '        manager.setLockTaskPackages(admin(activity), arrayOf(activity.packageName))'
if old_enter in d:
    d = d.replace(old_enter, '        if (!updateManagedLockAllowlist(activity, true)) return false', 1)
else:
    raise SystemExit("No se encontró la configuración de allowlist en enterManagedLock")
old_exit = '        manager.setLockTaskPackages(admin(activity), emptyArray())'
if old_exit in d:
    d = d.replace(old_exit, '        if (!updateManagedLockAllowlist(activity, false)) return false', 1)
else:
    raise SystemExit("No se encontró la limpieza de allowlist en exitManagedLock")
device.write_text(d)

# Restore saved restriction state and query the server on every app launch.
a = main.read_text()
a = a.replace('var isRestricted by remember { mutableStateOf(false) }',
              'var isRestricted by remember { mutableStateOf(prefs.isRestricted) }')
if 'LaunchedEffect(hasCredentials)' not in a:
    anchor = '        val isOwner = DeviceActions.isDeviceOwner(this@MainActivity)\\n'
    if anchor not in a:
        raise SystemExit("No se encontró el punto para sincronización automática en MainActivity")
    effect = '''\\n        LaunchedEffect(hasCredentials) {\\n            if (hasCredentials) {\\n                try {\\n                    val id = deviceId ?: prefs.deviceId ?: error("No hay ID de dispositivo")\\n                    val token = prefs.token ?: error("No hay credencial guardada")\\n                    val state = buildApi(server).sync(mapOf("deviceId" to id, "deviceToken" to token))\\n                    lastStatus = state.status\\n                    contractStatus = state.contractStatus\\n                    serverMessage = state.message.orEmpty()\\n                    when {\\n                        state.status.equals("LOCKED", true) && state.contractStatus.equals("ACTIVE", true) -> {\\n                            val applied = DeviceActions.enterManagedLock(this@MainActivity)\\n                            prefs.isRestricted = applied\\n                            isRestricted = applied\\n                            info = if (applied) "Orden LOCKED confirmada. Restricción administrada activada."\\n                                   else "Android no permitió activar el modo administrado."\\n                        }\\n                        state.status.equals("ACTIVE", true) -> {\\n                            val released = DeviceActions.exitManagedLock(this@MainActivity)\\n                            if (released) {\\n                                prefs.isRestricted = false\\n                                isRestricted = false\\n                                info = "Estado ACTIVE confirmado. Restricción administrada retirada."\\n                            } else {\\n                                info = "ACTIVE confirmado, pero Android no permitió retirar el modo administrado."\\n                            }\\n                        }\\n                        else -> info = "Estado consultado: ${state.status} / contrato ${state.contractStatus}. No se modificó la restricción."\\n                    }\\n                } catch (_: Exception) {\\n                    info = "Sin conexión para verificar el estado; se conserva la última restricción conocida."\\n                }\\n            }\\n        }\\n\\n'''
    a = a.replace(anchor, anchor + effect, 1)
a = a.replace('                                      isRestricted = true\\n', '                                      prefs.isRestricted = true\\n                                      isRestricted = true\\n')
a = a.replace('                                      prefs.isRestricted = false\\n                                      prefs.isRestricted = false\\n                                      isRestricted = false\\n', '                                      prefs.isRestricted = false\\n                                      isRestricted = false\\n')
main.write_text(a)

# Version 1.0.5 / code 6 for safe in-place update.
b = build.read_text()
b, n1 = re.subn(r'(?m)^(\\s*versionCode\\s*=\\s*)\\d+', r'\\g<1>6', b, count=1)
b, n2 = re.subn(r'(?m)^(\\s*versionName\\s*=\\s*)"[^"]+"', r'\\g<1>"1.0.5"', b, count=1)
if n1 != 1 or n2 != 1:
    raise SystemExit(f"No se pudo incrementar la versión: code={n1}, name={n2}")
build.write_text(b)
print("HOME persistente, sincronización al abrir y versión 1.0.5 configurados.")
