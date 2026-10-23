package app.netrunner.jade;

import android.app.admin.DeviceAdminReceiver;

/**
 * Device admin for kiosk mode. Becoming device owner (once, from a computer) turns screen pinning into a
 * real lock and makes Jade Link the permanent home screen:
 *   adb shell dpm set-device-owner app.netrunner.jadelink/app.netrunner.jade.AdminReceiver
 */
public class AdminReceiver extends DeviceAdminReceiver { }
