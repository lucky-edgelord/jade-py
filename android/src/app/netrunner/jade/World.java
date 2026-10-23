package app.netrunner.jade;

import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.hardware.Sensor;
import android.hardware.SensorEvent;
import android.hardware.SensorEventListener;
import android.hardware.SensorManager;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraManager;
import android.net.wifi.WifiInfo;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.VibrationEffect;
import android.os.Vibrator;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Jade's simple senses: every hardware sensor, battery, Wi-Fi signal, the flashlight and vibration. Senses adds the
 * camera, microphone and location on top. Every call is wrapped so a missing sensor or permission is a JSON error, never a crash.
 */
class World {
    private final Context a;
    private final Map<String, SensorEventListener> listeners = new HashMap<String, SensorEventListener>();
    private final Map<String, String> last = new HashMap<String, String>();
    World(Context a) { this.a = a; }

    static String json(String s) {
        StringBuilder b = new StringBuilder("\"");
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c == '"' || c == '\\') b.append('\\').append(c);
            else if (c < 32) b.append(' ');
            else b.append(c);
        }
        return b.append('"').toString();
    }

    static String err(String m) { return "{\"error\":" + json(m) + "}"; }

    static String key(Sensor s) {
        String t = s.getStringType();
        if (t == null || t.length() == 0) return "type" + s.getType();
        int i = t.lastIndexOf('.');
        return i >= 0 ? t.substring(i + 1) : t;
    }

    // ---------- sensors ----------
    SensorManager sm() { return (SensorManager) a.getSystemService(Context.SENSOR_SERVICE); }

    String sensorList() {
        try {
            List<Sensor> all = sm().getSensorList(Sensor.TYPE_ALL);
            StringBuilder b = new StringBuilder("[");
            for (int i = 0; i < all.size(); i++) {
                Sensor s = all.get(i);
                if (i > 0) b.append(',');
                b.append("{\"key\":").append(json(key(s))).append(",\"name\":").append(json(String.valueOf(s.getName())))
                        .append(",\"type\":").append(s.getType()).append(",\"vendor\":").append(json(String.valueOf(s.getVendor())))
                        .append(",\"range\":").append(num(s.getMaximumRange())).append(",\"resolution\":").append(num(s.getResolution()))
                        .append(",\"minDelayUs\":").append(s.getMinDelay()).append('}');
            }
            return b.append(']').toString();
        } catch (Throwable e) { return err(String.valueOf(e.getMessage())); }
    }

    static String num(float f) { return Float.isNaN(f) || Float.isInfinite(f) ? "null" : String.valueOf(f); }

    Sensor find(String k) {
        List<Sensor> all = sm().getSensorList(Sensor.TYPE_ALL);
        for (Sensor s : all) if (key(s).equals(k)) return s;
        for (Sensor s : all) if (String.valueOf(s.getName()).equals(k)) return s;
        return null;
    }

    /** Start listening to one sensor (by the key from sensorList). periodMs 0 = fastest. */
    synchronized String sensorStart(final String k, int periodMs) {
        try {
            if (listeners.containsKey(k)) return "{\"ok\":true,\"already\":true}";
            Sensor s = find(k);
            if (s == null) return err("no sensor called " + k);
            SensorEventListener l = new SensorEventListener() {
                @Override public void onSensorChanged(SensorEvent e) {
                    StringBuilder b = new StringBuilder("{\"t\":").append(System.currentTimeMillis()).append(",\"accuracy\":").append(e.accuracy).append(",\"values\":[");
                    for (int i = 0; i < e.values.length; i++) { if (i > 0) b.append(','); b.append(num(e.values[i])); }
                    synchronized (World.this) { last.put(k, b.append("]}").toString()); }
                }
                @Override public void onAccuracyChanged(Sensor sensor, int accuracy) { }
            };
            int us = periodMs <= 0 ? SensorManager.SENSOR_DELAY_FASTEST : periodMs * 1000;
            if (!sm().registerListener(l, s, us)) return err("the system refused to start " + k + " (it may need a permission)");
            listeners.put(k, l);
            return "{\"ok\":true}";
        } catch (Throwable e) { return err(String.valueOf(e.getMessage())); }
    }

    synchronized String sensorRead(String k) {
        String v = last.get(k);
        return v == null ? (listeners.containsKey(k) ? "{\"pending\":true}" : err(k + " is not started")) : v;
    }

    synchronized String sensorStop(String k) {
        try {
            SensorEventListener l = listeners.remove(k);
            if (l != null) sm().unregisterListener(l);
            last.remove(k);
            return "{\"ok\":true}";
        } catch (Throwable e) { return err(String.valueOf(e.getMessage())); }
    }

    synchronized void sensorsStopAll() {
        try { for (SensorEventListener l : listeners.values()) sm().unregisterListener(l); } catch (Throwable ignored) { }
        listeners.clear(); last.clear();
    }

    // ---------- battery & Wi-Fi ----------
    String battery() {
        try {
            Intent i = a.registerReceiver(null, new IntentFilter("android.intent.action.BATTERY_CHANGED"));
            if (i == null) return err("no battery information");
            int level = i.getIntExtra("level", -1), scale = i.getIntExtra("scale", 100);
            int status = i.getIntExtra("status", 1), plugged = i.getIntExtra("plugged", 0);
            int temp = i.getIntExtra("temperature", Integer.MIN_VALUE), volt = i.getIntExtra("voltage", -1);
            String[] st = {"unknown", "unknown", "charging", "discharging", "not charging", "full"};
            String src = plugged == 0 ? "battery" : (plugged & 1) != 0 ? "ac" : (plugged & 2) != 0 ? "usb" : (plugged & 4) != 0 ? "wireless" : "other";
            return "{\"percent\":" + (level < 0 ? "null" : String.valueOf(Math.round(level * 100f / Math.max(1, scale)))) + ",\"status\":" + json(status >= 0 && status < st.length ? st[status] : "unknown")
                    + ",\"power\":" + json(src) + ",\"temperatureC\":" + (temp == Integer.MIN_VALUE ? "null" : String.valueOf(temp / 10.0)) + ",\"millivolts\":" + (volt < 0 ? "null" : String.valueOf(volt)) + "}";
        } catch (Throwable e) { return err(String.valueOf(e.getMessage())); }
    }

    String wifi() {
        try {
            WifiManager w = (WifiManager) a.getApplicationContext().getSystemService(Context.WIFI_SERVICE);
            if (w == null) return err("no Wi-Fi hardware");
            boolean on = w.isWifiEnabled();
            WifiInfo i = on ? w.getConnectionInfo() : null;
            if (i == null) return "{\"enabled\":" + on + ",\"connected\":false}";
            int rssi = i.getRssi();
            String ssid = String.valueOf(i.getSSID());
            boolean hidden = ssid.indexOf("unknown ssid") >= 0;   // Android hides the name until the location permission is granted
            return "{\"enabled\":true,\"connected\":" + (rssi > -127) + ",\"ssid\":" + (hidden ? "null" : json(ssid.replace("\"", ""))) + ",\"bssid\":" + json(String.valueOf(i.getBSSID()))
                    + ",\"rssi\":" + rssi + ",\"linkMbps\":" + i.getLinkSpeed() + ",\"frequencyMHz\":" + i.getFrequency() + "}";
        } catch (Throwable e) { return err(String.valueOf(e.getMessage())); }
    }

    // ---------- flashlight & vibration ----------
    private String torchId;
    private boolean torchOn;

    private String findTorch() throws Exception {
        if (torchId != null) return torchId;
        CameraManager cm = (CameraManager) a.getSystemService("camera");
        String fallback = null;
        for (String id : cm.getCameraIdList()) {
            CameraCharacteristics cc = cm.getCameraCharacteristics(id);
            Boolean flash = cc.get(CameraCharacteristics.FLASH_INFO_AVAILABLE);
            if (flash == null || !flash.booleanValue()) continue;
            Integer facing = cc.get(CameraCharacteristics.LENS_FACING);
            if (facing != null && facing.intValue() == CameraCharacteristics.LENS_FACING_BACK) { torchId = id; return id; }
            if (fallback == null) fallback = id;
        }
        torchId = fallback;
        return torchId;
    }

    /** Switch the flashlight. No permission is needed for the torch itself. */
    synchronized String torch(boolean on) {
        try {
            String id = findTorch();
            if (id == null) return err("this phone has no flashlight");
            ((CameraManager) a.getSystemService("camera")).setTorchMode(id, on);
            torchOn = on;
            return "{\"ok\":true,\"on\":" + on + "}";
        } catch (Throwable e) { return err("flashlight: " + e.getMessage() + " (another app may be using the camera)"); }
    }

    /** Vibrate with a pattern of milliseconds: on, off, on, off... (at most 30 seconds in all). An empty pattern stops it. */
    String vibrate(String csv) {
        try {
            Vibrator v = (Vibrator) a.getSystemService("vibrator");
            if (v == null || !v.hasVibrator()) return err("this phone has no vibration motor");
            if (csv == null || csv.trim().length() == 0) { v.cancel(); return "{\"ok\":true,\"stopped\":true}"; }
            String[] parts = csv.split(",");
            long[] pat = new long[parts.length + 1];
            long total = 0;
            for (int i = 0; i < parts.length; i++) {
                long ms = Math.max(0, Math.min(10000, Long.parseLong(parts[i].trim())));
                total += ms;
                pat[i + 1] = ms;      // pat[0] = 0: start now
            }
            if (total > 30000) return err("vibration is limited to 30 seconds in all");
            if (Build.VERSION.SDK_INT >= 26) v.vibrate(VibrationEffect.createWaveform(pat, -1)); else v.vibrate(pat, -1);
            return "{\"ok\":true,\"ms\":" + total + "}";
        } catch (Throwable e) { return err("vibrate: " + e.getMessage()); }
    }
    void destroy() {
        if (torchOn) torch(false);
        sensorsStopAll();
    }
}
