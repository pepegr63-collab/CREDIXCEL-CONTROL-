#!/usr/bin/env python3
"""Apply delayed Lock Task verification to the generated fixed12 Android source."""
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
main_path = root / "android/app/src/main/java/com/credixcel/control/MainActivity.kt"
device_path = root / "android/app/src/main/java/com/credixcel/control/device/DeviceActions.kt"
if not main_path.is_file() or not device_path.is_file():
    raise SystemExit("Missing expected generated Android source files")
s = main_path.read_text(encoding="utf-8")

if "import kotlinx.coroutines.delay\n" not in s:
    marker = "import kotlinx.coroutines.Dispatchers\n"
    if marker not in s:
        raise SystemExit("Missing coroutine import marker")
    s = s.replace(marker, marker + "import kotlinx.coroutines.delay\n", 1)
if "private var lockTaskEntryInProgress = false" not in s:
    marker = "    private var commandProcessing = false\n"
    if marker not in s:
        raise SystemExit("Missing commandProcessing field")
    s = s.replace(marker, marker + "    private var lockTaskEntryInProgress = false\n", 1)

old = '''    private fun enterManagedLockTask(): Boolean {
        if (!DeviceActions.isDeviceOwner(this) || !DeviceActions.configureKiosk(this) || !DeviceActions.isLockTaskPermitted(this)) return false
        lockScreenVisible = true
        return runCatching {
            val manager = getSystemService(ActivityManager::class.java)
            if (manager.lockTaskModeState == ActivityManager.LOCK_TASK_MODE_NONE) startLockTask()
            getSystemService(ActivityManager::class.java).lockTaskModeState == ActivityManager.LOCK_TASK_MODE_LOCKED
        }.getOrDefault(false)
    }

    private fun processPendingCommand() {
        if (commandProcessing || !::prefs.isInitialized) return
        val commandId = prefs.pendingCommandId ?: return
        val action = prefs.pendingCommandAction ?: return
        commandProcessing = true
        val success: Boolean
        val result: String
        when (action) {
            "LOCK_DEVICE" -> {
                result = activateKiosk()
                success = prefs.protectionActive && getSystemService(ActivityManager::class.java).lockTaskModeState == ActivityManager.LOCK_TASK_MODE_LOCKED
            }
            "UNLOCK_DEVICE" -> {
                result = releaseKiosk()
                success = !prefs.protectionActive && getSystemService(ActivityManager::class.java).lockTaskModeState == ActivityManager.LOCK_TASK_MODE_NONE
            }
            else -> { result = "UNSUPPORTED_COMMAND:$action"; success = false }
        }
        lifecycleScope.launch {
            val acknowledged = withContext(Dispatchers.IO) {
                runCatching {
                    val id = prefs.deviceId ?: error("MISSING_DEVICE_ID")
                    val deviceToken = prefs.token ?: error("MISSING_DEVICE_TOKEN")
                    buildApi(prefs.serverUrl).acknowledgeCommand(CommandAckRequest(id, deviceToken, commandId, success, result)).acknowledged
                }.getOrDefault(false)
            }
            if (acknowledged) prefs.clearPendingCommand()
            commandProcessing = false
        }
    }
'''
new = '''    private fun enterManagedLockTask() {
        if (lockTaskEntryInProgress || !::prefs.isInitialized) return
        lockTaskEntryInProgress = true
        lifecycleScope.launch {
            try {
                if (!DeviceActions.isDeviceOwner(this@MainActivity) ||
                    !DeviceActions.configureKiosk(this@MainActivity) ||
                    !DeviceActions.isLockTaskPermitted(this@MainActivity) ||
                    !DeviceActions.applyFinancingProtection(this@MainActivity)) {
                    lockScreenVisible = true
                    return@launch
                }
                lockScreenVisible = true
                startLockTaskAndWait()
            } finally {
                lockTaskEntryInProgress = false
            }
        }
    }

    private suspend fun startLockTaskAndWait(): Boolean {
        val initial = getSystemService(ActivityManager::class.java).lockTaskModeState
        if (initial == ActivityManager.LOCK_TASK_MODE_LOCKED) return true
        if (initial == ActivityManager.LOCK_TASK_MODE_NONE) {
            try { startLockTask() } catch (_: Exception) { return false }
        }
        repeat(30) {
            if (getSystemService(ActivityManager::class.java).lockTaskModeState == ActivityManager.LOCK_TASK_MODE_LOCKED) return true
            delay(100)
        }
        return getSystemService(ActivityManager::class.java).lockTaskModeState == ActivityManager.LOCK_TASK_MODE_LOCKED
    }

    private fun processPendingCommand() {
        if (commandProcessing || !::prefs.isInitialized) return
        val commandId = prefs.pendingCommandId ?: return
        val action = prefs.pendingCommandAction ?: return
        commandProcessing = true
        lifecycleScope.launch {
            try {
                val (success, result) = when (action) {
                    "LOCK_DEVICE" -> {
                        val message = activateKiosk()
                        (prefs.protectionActive && getSystemService(ActivityManager::class.java).lockTaskModeState == ActivityManager.LOCK_TASK_MODE_LOCKED) to message
                    }
                    "UNLOCK_DEVICE" -> {
                        val message = releaseKiosk()
                        (!prefs.protectionActive && getSystemService(ActivityManager::class.java).lockTaskModeState == ActivityManager.LOCK_TASK_MODE_NONE) to message
                    }
                    else -> false to "UNSUPPORTED_COMMAND:$action"
                }
                val acknowledged = withContext(Dispatchers.IO) {
                    runCatching {
                        val id = prefs.deviceId ?: error("MISSING_DEVICE_ID")
                        val deviceToken = prefs.token ?: error("MISSING_DEVICE_TOKEN")
                        buildApi(prefs.serverUrl).acknowledgeCommand(CommandAckRequest(id, deviceToken, commandId, success, result)).acknowledged
                    }.getOrDefault(false)
                }
                if (acknowledged) prefs.clearPendingCommand()
            } finally {
                commandProcessing = false
            }
        }
    }
'''
if old in s:
    s = s.replace(old, new, 1)
elif "private suspend fun startLockTaskAndWait()" not in s:
    raise SystemExit("Could not find the expected Lock Task/command-processing block")

old_activate = '''    private fun activateKiosk(): String {
        if (!DeviceActions.isDeviceOwner(this)) return "CREDIXCEL no tiene autorización Device Owner."
        if (!DeviceActions.configureKiosk(this)) return "Android rechazó la configuración de la pantalla administrativa."
        if (!DeviceActions.applyFinancingProtection(this)) {
            DeviceActions.clearKioskConfiguration(this)
            DeviceActions.releaseFinancingProtection(this)
            return "No se pudieron aplicar todas las políticas; no se confirma el bloqueo."
        }
        prefs.protectionActive = true
        return try {
            val manager = getSystemService(ActivityManager::class.java)
            if (manager.lockTaskModeState == ActivityManager.LOCK_TASK_MODE_NONE) startLockTask()
            if (manager.lockTaskModeState == ActivityManager.LOCK_TASK_MODE_LOCKED) {
                lockScreenVisible = true
                "LOCK_SCREEN_ACTIVE: pantalla administrativa visible y Lock Task verificado."
            } else {
                runCatching { if (manager.lockTaskModeState != ActivityManager.LOCK_TASK_MODE_NONE) stopLockTask() }
                DeviceActions.clearKioskConfiguration(this)
                DeviceActions.releaseFinancingProtection(this)
                prefs.protectionActive = false
                lockScreenVisible = false
                "LOCK_SCREEN_NOT_CONFIRMED: Android no confirmó Lock Task."
            }
        } catch (e: Exception) {
            runCatching { if (getSystemService(ActivityManager::class.java).lockTaskModeState != ActivityManager.LOCK_TASK_MODE_NONE) stopLockTask() }
            DeviceActions.clearKioskConfiguration(this)
            DeviceActions.releaseFinancingProtection(this)
            prefs.protectionActive = false
            lockScreenVisible = false
            "LOCK_SCREEN_NOT_CONFIRMED: ${e.localizedMessage ?: e.javaClass.simpleName}"
        }
    }
'''
new_activate = '''    private suspend fun activateKiosk(): String {
        if (!DeviceActions.isDeviceOwner(this)) return "CREDIXCEL no tiene autorización Device Owner."
        prefs.protectionActive = true
        lockScreenVisible = true
        if (!DeviceActions.configureKiosk(this)) {
            return "LOCK_TASK_CONFIGURATION_FAILED: Android no confirmó la allowlist de CREDIXCEL."
        }
        if (!DeviceActions.applyFinancingProtection(this)) {
            return "LOCK_POLICIES_INCOMPLETE: no se pudieron aplicar todas las políticas; se conserva la autorización local para diagnóstico."
        }
        return if (startLockTaskAndWait()) {
            lockScreenVisible = true
            "LOCK_SCREEN_ACTIVE: pantalla administrativa visible y Lock Task verificado."
        } else {
            val state = getSystemService(ActivityManager::class.java).lockTaskModeState
            val permitted = DeviceActions.isLockTaskPermitted(this)
            "LOCK_SCREEN_NOT_CONFIRMED: timeout esperando Lock Task (mode=$state, permitted=$permitted); se conserva la configuración para reintento."
        }
    }
'''
if old_activate in s:
    s = s.replace(old_activate, new_activate, 1)
elif "private suspend fun activateKiosk(): String" not in s:
    raise SystemExit("Could not find activateKiosk implementation")
main_path.write_text(s, encoding="utf-8")

d = device_path.read_text(encoding="utf-8")
if "catch (_: IllegalArgumentException)" not in d:
    marker = '''        } catch (_: IllegalStateException) {
            false
        }
    }

    /** Remove only the kiosk HOME preference'''
    replacement = '''        } catch (_: IllegalStateException) {
            false
        } catch (_: IllegalArgumentException) {
            false
        }
    }

    /** Remove only the kiosk HOME preference'''
    if marker not in d:
        raise SystemExit("Could not find configureKiosk exception block")
    d = d.replace(marker, replacement, 1)
device_path.write_text(d, encoding="utf-8")
print("Delayed Lock Task verification patch applied.")
