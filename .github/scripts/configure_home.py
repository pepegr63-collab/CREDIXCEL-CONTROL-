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

m = manifest.read_text()
pat = r'(<activity\s+android:name="\.MainActivity"[^>]*>)(.*?)(</activity>)'
match = re.search(pat, m, re.S)
if not match:
    raise SystemExit("No se encontró la declaración de MainActivity en el manifiesto")
body = match.group(2)
if 'android.intent.category.HOME' not in body:
    body += '''
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.HOME" />
                <category android:name="android.intent.category.DEFAULT" />
            </intent-filter>'''
    m = m[:match.start(2)] + body + m[match.end(2):]
manifest.write_text(m)

d = device.read_text()
helper = '''
    /** Configura Lock Task y HOME persistente desde Activity o Worker. */
    fun updateManagedLockAllowlist(context: Context, locked: Boolean): Boolean {
        if (!isDeviceOwner(context)) return false
        return try {
            val manager = dpm(context)
            if (locked) {
                manager.setLockTaskPackages(admin(context), arrayOf(context.packageName))
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                    manager.setLockTaskFeatures(
                        admin(context),
                        DevicePolicyManager.LOCK_TASK_FEATURE_GLOBAL_ACTIONS or
                            DevicePolicyManager.LOCK_TASK_FEATURE_KEYGUARD
                    )
                }
                val home = android.content.IntentFilter(android.content.Intent.ACTION_MAIN).apply {
                    addCategory(android.content.Intent.CATEGORY_HOME)
                    addCategory(android.content.Intent.CATEGORY_DEFAULT)
                }
                manager.addPersistentPreferredActivity(
                    admin(context), home,
                    ComponentName(context, com.credixcel.control.MainActivity::class.java)
                )
            } else {
                manager.clearPackagePersistentPreferredActivities(admin(context), context.packageName)
                manager.setLockTaskPackages(admin(context), emptyArray())
            }
            true
        } catch (_: Exception) { false }
    }
'''
helper_pat = r'(?ms)^    fun updateManagedLockAllowlist\(context: Context, locked: Boolean\): Boolean \{.*?^    \}'
if re.search(helper_pat, d):
    d = re.sub(helper_pat, helper.strip('\n'), d, count=1)
else:
    needle = '    fun lock(context: Context): Boolean {'
    if needle not in d:
        raise SystemExit("No se encontró el punto de inserción en DeviceActions.kt")
    d = d.replace(needle, helper + '\n' + needle, 1)

old_enter = '        manager.setLockTaskPackages(admin(activity), arrayOf(activity.packageName))'
if old_enter in d:
    d = d.replace(old_enter, '        if (!updateManagedLockAllowlist(activity, true)) return false', 1)
elif 'if (!updateManagedLockAllowlist(activity, true)) return false' not in d:
    raise SystemExit("No se encontró la configuración de allowlist en enterManagedLock")
old_exit = '        manager.setLockTaskPackages(admin(activity), emptyArray())'
if old_exit in d:
    d = d.replace(old_exit, '        if (!updateManagedLockAllowlist(activity, false)) return false', 1)
elif 'updateManagedLockAllowlist(activity, false)' not in d:
    raise SystemExit("No se encontró la limpieza de allowlist en exitManagedLock")
device.write_text(d)

a = main.read_text()
a = a.replace('var isRestricted by remember { mutableStateOf(false) }',
              'var isRestricted by remember { mutableStateOf(prefs.isRestricted) }')
if 'LaunchedEffect(hasCredentials)' not in a:
    anchor = '        val isOwner = DeviceActions.isDeviceOwner(this@MainActivity)\n'
    if anchor not in a:
        raise SystemExit("No se encontró el punto para sincronización automática en MainActivity")
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
                        state.status.equals("LOCKED", true) && state.contractStatus.equals("ACTIVE", true) -> {
                            val applied = DeviceActions.enterManagedLock(this@MainActivity)
                            prefs.isRestricted = applied
                            isRestricted = applied
                            info = if (applied) "Orden LOCKED confirmada. Restricción administrada activada."
                                   else "Android no permitió activar el modo administrado."
                        }
                        state.status.equals("ACTIVE", true) -> {
                            val released = DeviceActions.exitManagedLock(this@MainActivity)
                            if (released) {
                                prefs.isRestricted = false
                                isRestricted = false
                                info = "Estado ACTIVE confirmado. Restricción administrada retirada."
                            } else {
                                info = "ACTIVE confirmado, pero Android no permitió retirar el modo administrado."
                            }
                        }
                        else -> info = "Estado consultado: \${state.status} / contrato \${state.contractStatus}. No se modificó la restricción."
                    }
                } catch (_: Exception) {
                    info = "Sin conexión para verificar el estado; se conserva la última restricción conocida."
                }
            }
        }

'''
    a = a.replace(anchor, anchor + effect, 1)
a = a.replace('                                      prefs.isRestricted = false\n                                      prefs.isRestricted = false\n                                      isRestricted = false\n',
              '                                      prefs.isRestricted = false\n                                      isRestricted = false\n')
main.write_text(a)

b = build.read_text()
b, n1 = re.subn(r'(?m)^(\s*versionCode\s*=\s*)\d+', r'\g<1>6', b, count=1)
b, n2 = re.subn(r'(?m)^(\s*versionName\s*=\s*)"[^"]+"', r'\g<1>"1.0.5"', b, count=1)
if n1 != 1 or n2 != 1:
    raise SystemExit(f"No se pudo incrementar la versión: code={n1}, name={n2}")
build.write_text(b)
print("HOME persistente, sincronización al abrir y versión 1.0.5 configurados.")
