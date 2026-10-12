# CREDIXCEL Control v1 — Android DPC + Panel

This release adds official Android Enterprise policies for financed devices.

## Device protections

When the app is provisioned as **Device Owner**, CREDIXCEL can:

- Block uninstall of CREDIXCEL Control with `setUninstallBlocked()`.
- Block factory reset from Android Settings using `DISALLOW_FACTORY_RESET`.
- Block Safe Mode using `DISALLOW_SAFE_BOOT`.
- Configure Android Factory Reset Protection (FRP) on Android 11/API 30+ with `setFactoryResetProtectionPolicy()`.
- Lock the device with `lockNow()`.

Android documents `DISALLOW_FACTORY_RESET` as preventing a user from factory resetting from Settings, and notes that Device Owner can still perform an administrative wipe. Recovery/button behavior is manufacturer-dependent; FRP is the protection used after an untrusted reset when supported. See the official Android references linked below.

## FRP account configuration

Do **not** put a password in the APK. The FRP policy expects account identifiers understood by the FRP management agent. The production backend should store the approved CREDIXCEL administrator account ID(s) securely and provide them during device provisioning.

The code currently exposes `applyFrpPolicy(context, enabled, accountIds)` so the provisioning flow can supply those identifiers.

## Important provisioning requirement

A normal APK installed after setup cannot make itself Device Owner. The phone must be provisioned as a fully managed device during initial setup using an Android Enterprise supported enrollment method. Test devices can be provisioned with Android's development provisioning flow.

## Build

Open the `CREDIXCEL-Control` folder in Android Studio, allow Gradle sync, then build the debug APK from:

`android/app` → `assembleDebug`

The resulting APK will be under:

`android/app/build/outputs/apk/debug/`

## Official Android references

- DevicePolicyManager: https://developer.android.com/reference/android/app/admin/DevicePolicyManager
- FactoryResetProtectionPolicy: https://developer.android.com/reference/android/app/admin/FactoryResetProtectionPolicy
- Android Enterprise security / FRP: https://developer.android.com/work/dpc/security
- Dedicated-device restrictions: https://developer.android.com/work/dpc/dedicated-devices/cookbook
