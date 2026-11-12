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

# HOME solo se asigna a CREDIXCEL durante LOCKED.
m = manifest.read_text()
pat = r'(<activity\s+android:name="\.MainActivity"[^>]*>)(.*?)(</activity>)'
x = re.search(pat, m, re.S)
if not x:
    raise SystemExit("No se encontró MainActivity en el manifiesto")
body = x.group(2)
if "android.intent.category.HOME" not in body:
    body += '''
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.HOME" />
                <category android:name="android.intent.category.DEFAULT" />
            </intent-filter>'''
m = m[:x.start(2)] + body + m[x.end(2):]
manifest.write_text(m)

# Sustituye las implementaciones anteriores por una pareja coherente:
# HOME persistente al bloquear; borrado de HOME antes de salir de Lock Task.
d = device.read_text()
block = '''
    /** Permite HOME=CREDIXCEL solo durante una restricción confirmada. */
    fun updateManagedLockAllowlist(context: Context, locked: Boolean): Boolean {
        if (!isDeviceOwner(context)) return false
        return try {
            val manager = dpm(context)
            if (locked) {
                manager.setLockTaskPackages(admin(context), arrayOf(context.packageName))
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                    manager.setLockTaskFeatures(admin(context),
                        DevicePolicyManager.LOCK_TASK_FEATURE_GLOBAL_ACTIONS or
                        DevicePolicyManager.LOCK_TASK_FEATURE_KEYGUARD)
                }
                val filter = android.content.IntentFilter(Intent.ACTION_MAIN).apply {
                    addCategory(Intent.CATEGORY_HOME)
                    addCategory(Intent.CATEGORY_DEFAULT)
                }
                manager.addPersistentPreferredActivity(admin(context), filter,
                    ComponentName(context, com.credixcel.control.MainActivity::class.java))
            } else {
                manager.clearPackagePersistentPreferredActivities(admin(context), context.packageName)
                manager.setLockTaskPackages(admin(context), emptyArray())
            }
            true
        } catch (_: Exception) { false }
    }

    fun enterManagedLock(activity: android.app.Activity): Boolean {
        if (!updateManagedLockAllowlist(activity, true)) return false
        return try {
            val state = activity.getSystemService(android.app.ActivityManager::class.java).lockTaskModeState
            if (state == android.app.ActivityManager.LOCK_TASK_MODE_NONE) activity.startLockTask()
            true
        } catch (_: Exception) { false }
    }

    fun exitManagedLock(activity: android.app.Activity): Boolean {
        if (!isDeviceOwner(activity)) return false
        return try {
            val manager = dpm(activity)
            // Si no puede quitar la preferencia HOME, no liberar parcialmente.
            manager.clearPackagePersistentPreferredActivities(admin(activity), activity.packageName)
            val state = activity.getSystemService(android.app.ActivityManager::class.java).lockTaskModeState
            if (state != android.app.ActivityManager.LOCK_TASK_MODE_NONE) activity.stopLockTask()
            manager.setLockTaskPackages(admin(activity), emptyArray())
            activity.startActivity(Intent(Intent.ACTION_MAIN).apply {
                addCategory(Intent.CATEGORY_HOME)
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            })
            true
        } catch (_: Exception) { false }
    }
'''
# Remove previous helper section, whatever its earlier variant.
start = re.search(r'\n\s*/\*\* (?:Activa Lock Task administrado|Actualiza la lista de Lock Task).*?(?=\n\s*fun lock\(context: Context\): Boolean \{)', d, re.S)
if start:
    d = d[:start.start()] + "\n" + block + "\n" + d[start.end():]
else:
    needle = "    fun lock(context: Context): Boolean {"
    if needle not in d:
        raise SystemExit("No se encontró punto de inserción en DeviceActions.kt")
    d = d.replace(needle, block + "\n" + needle, 1)
if "import android.content.ComponentName" not in d:
    d = d.replace("import android.content.Context\n", "import android.content.Context\nimport android.content.ComponentName\n")
device.write_text(d)

a = main.read_text()
a = a.replace("var isRestricted by remember { mutableStateOf(false) }",
              "var isRestricted by remember { mutableStateOf(prefs.isRestricted) }")
# Re-enter Lock Task immediately when Android launches the temporary HOME after reboot.
if "if (prefs.isRestricted && DeviceActions.isDeviceOwner(this))" not in a:
    anchor = "        val prefs = Prefs(this)\n"
    if anchor not in a:
        raise SystemExit("No se encontró Prefs en onCreate")
    a = a.replace(anchor, anchor + '''        if (prefs.isRestricted && DeviceActions.isDeviceOwner(this)) {
            DeviceActions.enterManagedLock(this)
        }
''', 1)
# First sync after boot/open: only both ACTIVE values can release.
if "LaunchedEffect(hasCredentials)" not in a:
    anchor = "        val isOwner = DeviceActions.isDeviceOwner(this@MainActivity)\n"
    if anchor not in a:
        raise SystemExit("No se encontró punto para sincronización inicial")
    effect = '''
        LaunchedEffect(hasCredentials) {
            if (hasCredentials) {
                try {
                    val id = deviceId ?: prefs.deviceId ?: error("No hay ID de dispositivo")
                    val token = prefs.token ?: error("No hay credencial guardada")
                    val state = buildApi(server).sync(mapOf("deviceId" to id, "deviceToken" to token))
                    lastStatus = state.status
                    contractStatus = state.contractStatus
                    serverMessage = state.message.orEmpty()
                    when {
                        state.status.equals("LOCKED", true) -> {
                            val applied = DeviceActions.enterManagedLock(this@MainActivity)
                            prefs.isRestricted = applied
                            isRestricted = applied
                            info = if (applied) "Orden LOCKED confirmada. Restricción administrada activada."
                                   else "Android no permitió activar el modo administrado."
                        }
                        state.status.equals("ACTIVE", true) && state.contractStatus.equals("ACTIVE", true) -> {
                            val released = DeviceActions.exitManagedLock(this@MainActivity)
                            if (released) {
                                prefs.isRestricted = false
                                isRestricted = false
                                info = "Estado ACTIVE confirmado. Restricción administrada retirada."
                            } else info = "ACTIVE confirmado, pero Android no permitió retirar el modo administrado."
                        }
                        else -> info = "Estado consultado: " + state.status + " / contrato " + state.contractStatus + ". Se conserva la restricción anterior."
                    }
                } catch (_: Exception) {
                    info = "Sin conexión para verificar el estado; se conserva la última restricción conocida."
                }
            }
        }

'''
    a = a.replace(anchor, anchor + effect, 1)
a = a.replace('if (state.status.equals("ACTIVE", true)) {',
              'if (state.status.equals("ACTIVE", true) && state.contractStatus.equals("ACTIVE", true)) {')
a = a.replace('state.status.equals("ACTIVE", true) -> {',
              'state.status.equals("ACTIVE", true) && state.contractStatus.equals("ACTIVE", true) -> {')
# Persist foreground state transitions, so boot recovery never depends on a fresh connection.
lines, out = a.splitlines(), []
for line in lines:
    stripped = line.strip()
    if stripped in ("isRestricted = true", "isRestricted = false"):
        indent = line[:len(line) - len(line.lstrip())]
        persisted = "prefs." + stripped
        if not out or out[-1].strip() != persisted:
            out.append(indent + persisted)
    out.append(line)
main.write_text("\n".join(out) + "\n")

b = build.read_text()
b, n1 = re.subn(r'(?m)^(\s*versionCode\s*=\s*)\d+', r'\g<1>10', b, count=1)
b, n2 = re.subn(r'(?m)^(\s*versionName\s*=\s*)"[^"]+"', r'\g<1>"1.0.9"', b, count=1)
if n1 != 1 or n2 != 1:
    raise SystemExit(f"No se pudo versionar: code={n1}, name={n2}")
build.write_text(b)
print("Configurado HOME temporal, recuperación de Lock Task al reinicio y liberación solo con ambos estados ACTIVE. Versión 1.0.9.")
