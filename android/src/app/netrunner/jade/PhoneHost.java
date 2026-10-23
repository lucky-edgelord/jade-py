package app.netrunner.jade;

import android.content.ClipData;
import android.content.Context;
import android.content.ClipboardManager;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.database.Cursor;
import android.media.AudioAttributes;
import android.media.AudioManager;
import android.media.Ringtone;
import android.media.RingtoneManager;
import android.net.Uri;
import android.os.Build;
import android.provider.Settings;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.telecom.TelecomManager;
import android.telephony.CellSignalStrength;
import android.telephony.SignalStrength;
import android.telephony.SmsManager;
import android.telephony.TelephonyManager;
import android.view.Window;
import android.view.WindowManager;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/**
 * What a phone can do besides sensing: alarms and timers, ringing, sounds, volume, calls, texts, contacts, clipboard,
 * launching apps, brightness, radio status, Bluetooth, media keys. One entry point, run(op, json), always answering
 * with a JSON string. In Jade Link it runs inside the link service; asking the user first (calls, texts) happens in
 * jade-py (or here, through Asker) before it gets this far.
 */
final class PhoneHost {
    private final Context a;
    private final android.os.Handler main = new android.os.Handler(android.os.Looper.getMainLooper());
    private Ringtone ring;
    private int ringToken;
    final Sounds sounds;

    PhoneHost(Context a) { this.a = a; this.sounds = new Sounds(a); }

    private static String err(String m) { return "{\"error\":" + World.json(m) + "}"; }
    private static final String OK = "{\"ok\":true}";

    String run(String op, String json) {
        try {
            JSONObject o = new JSONObject(json == null || json.length() == 0 ? "{}" : json);
            if (op.equals("alarm_set")) return alarmSet(o);
            if (op.equals("timer_set")) return timerSet(o);
            if (op.equals("alarms_show")) return start(new Intent("android.intent.action.SHOW_ALARMS"));
            if (op.equals("ring")) return ring(o.optInt("seconds", 30));
            if (op.equals("ring_stop")) return ringStop();
            if (op.equals("volume")) return volume(o);
            if (op.equals("call")) return call(o.optString("number", ""));
            if (op.equals("answer")) return answer();
            if (op.equals("hangup")) return hangup();
            if (op.equals("speaker")) return speaker(o.optBoolean("on", true));
            if (op.equals("call_state")) return callState();
            if (op.equals("call_log")) return callLog(o.optInt("limit", 10));
            if (op.equals("sms_send")) return smsSend(o.optString("to", ""), o.optString("text", ""));
            if (op.equals("sms_read")) return smsRead(o);
            if (op.equals("contacts")) return contacts(o.optString("query", ""), o.optInt("limit", 15));
            if (op.equals("clipboard_get")) return clipGet();
            if (op.equals("clipboard_set")) return clipSet(o.optString("text", ""));
            if (op.equals("open_url")) return openUrl(o.optString("url", ""));
            if (op.equals("apps")) return apps(o.optString("query", ""));
            if (op.equals("app_launch")) return appLaunch(o.optString("package", ""));
            if (op.equals("brightness")) return brightness(o.optDouble("level", -1));
            if (op.equals("keep_awake")) return keepAwake(o.optBoolean("on", true));
            if (op.equals("radio")) return radio();
            if (op.equals("bluetooth")) return bluetooth();
            if (op.equals("bt_scan")) return btScan(o.optInt("seconds", 10));
            if (op.equals("bt_pair")) return btPair(o.optString("address", ""), o.optString("name", ""));
            if (op.equals("bt_unpair")) return btUnpair(o.optString("address", ""), o.optString("name", ""));
            if (op.equals("bt_connect")) return btConnect(o.optString("address", ""), o.optString("name", ""), true);
            if (op.equals("bt_disconnect")) return btConnect(o.optString("address", ""), o.optString("name", ""), false);
            if (op.equals("bt_power")) return btPower(o.optBoolean("on", true));
            if (op.equals("bt_settings")) return start(new Intent("android.settings.BLUETOOTH_SETTINGS"));
            if (op.equals("media")) return mediaKey(o.optString("command", "play_pause"));
            if (op.equals("sound_play")) return sounds.play(o);
            if (op.equals("tone")) return sounds.tone(o);
            if (op.equals("sound_stop")) return sounds.stop();
            if (op.equals("now_playing")) return nowPlaying();
            return err("unknown phone action " + op);
        } catch (Throwable e) { return err(op + ": " + e.getMessage()); }
    }

    /** Run on the UI thread and wait for the answer (starting activities and window changes need it). */
    private String ui(final Callable<String> c) {
        final String[] out = new String[1];
        final CountDownLatch done = new CountDownLatch(1);
        main.post(new Runnable() {
            @Override public void run() {
                try { out[0] = c.call(); } catch (Throwable e) { out[0] = err(String.valueOf(e.getMessage())); } finally { done.countDown(); }
            }
        });
        try { if (!done.await(6, TimeUnit.SECONDS)) return err("the phone did not answer in time"); } catch (InterruptedException e) { return err("interrupted"); }
        return out[0];
    }

    private String start(final Intent i) {
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        return ui(new Callable<String>() { public String call() { return Starter.start(a, i); } });
    }

    // ---------- alarms, timers, ringing, volume ----------
    private String alarmSet(JSONObject o) {
        int h = o.optInt("hour", -1), m = o.optInt("minute", 0);
        if (h < 0 || h > 23 || m < 0 || m > 59) return err("hour is 0-23 and minute 0-59");
        Intent i = new Intent("android.intent.action.SET_ALARM");
        i.putExtra("android.intent.extra.alarm.HOUR", h);
        i.putExtra("android.intent.extra.alarm.MINUTES", m);
        i.putExtra("android.intent.extra.alarm.MESSAGE", o.optString("label", "Jade"));
        i.putExtra("android.intent.extra.alarm.SKIP_UI", o.optBoolean("silent", true));
        i.putExtra("android.intent.extra.alarm.VIBRATE", true);
        JSONArray d = o.optJSONArray("days");
        if (d != null && d.length() > 0) { ArrayList<Integer> days = new ArrayList<Integer>(); for (int k = 0; k < d.length(); k++) days.add(Integer.valueOf(d.optInt(k, 1))); i.putIntegerArrayListExtra("android.intent.extra.alarm.DAYS", days); }
        String r = start(i);
        return r.equals(OK) ? "{\"ok\":true,\"alarm\":\"" + (h < 10 ? "0" : "") + h + ":" + (m < 10 ? "0" : "") + m + "\"}" : r;
    }

    private String timerSet(JSONObject o) {
        int s = o.optInt("seconds", 0);
        if (s < 1 || s > 86400) return err("seconds is 1 to 86400");
        Intent i = new Intent("android.intent.action.SET_TIMER");
        i.putExtra("android.intent.extra.alarm.LENGTH", s);
        i.putExtra("android.intent.extra.alarm.MESSAGE", o.optString("label", "Jade"));
        i.putExtra("android.intent.extra.alarm.SKIP_UI", o.optBoolean("silent", true));
        String r = start(i);
        return r.equals(OK) ? "{\"ok\":true,\"seconds\":" + s + "}" : r;
    }

    private synchronized String ring(int seconds) {
        try {
            ringStop();
            int secs = Math.max(1, Math.min(300, seconds));
            Uri u = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM);
            if (u == null) u = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE);
            Ringtone r = RingtoneManager.getRingtone(a, u);
            if (r == null) return err("this phone has no alarm sound");
            r.setAudioAttributes(new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ALARM).setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build());
            if (Build.VERSION.SDK_INT >= 28) r.setLooping(true);
            r.play();
            ring = r;
            final int token = ++ringToken;
            final long ms = secs * 1000L;
            Thread t = new Thread(new Runnable() { public void run() { try { Thread.sleep(ms); } catch (InterruptedException ignored) { } synchronized (PhoneHost.this) { if (token == ringToken) ringStop(); } } });
            t.setDaemon(true);
            t.start();
            return "{\"ok\":true,\"seconds\":" + secs + "}";
        } catch (Throwable e) { return err("ring: " + e.getMessage()); }
    }

    private synchronized String ringStop() {
        ringToken++;
        try { if (ring != null) ring.stop(); } catch (Throwable ignored) { }
        ring = null;
        return OK;
    }

    private static final String[] STREAMS = { "voice_call", "system", "ring", "media", "alarm", "notification" };
    private static final int[] STREAM_IDS = { 0, 1, 2, 3, 4, 5 };

    private String volume(JSONObject o) {
        AudioManager am = (AudioManager) a.getSystemService("audio");
        String which = o.optString("stream", "media");
        if (o.has("level")) {
            int idx = -1;
            for (int i = 0; i < STREAMS.length; i++) if (STREAMS[i].equals(which)) idx = i;
            if (idx < 0) return err("stream is one of media, ring, alarm, notification, voice_call, system");
            double lv = Math.max(0, Math.min(1, o.optDouble("level", 0.5)));
            try { am.setStreamVolume(STREAM_IDS[idx], (int) Math.round(lv * am.getStreamMaxVolume(STREAM_IDS[idx])), 0); }
            catch (SecurityException e) { return err("Android would not change that volume (Do Not Disturb may be on)"); }
        }
        StringBuilder b = new StringBuilder("{");
        for (int i = 0; i < STREAMS.length; i++) {
            int max = Math.max(1, am.getStreamMaxVolume(STREAM_IDS[i])), cur = am.getStreamVolume(STREAM_IDS[i]);
            if (i > 0) b.append(',');
            b.append('"').append(STREAMS[i]).append("\":{\"level\":").append(Math.round(100.0 * cur / max) / 100.0).append(",\"index\":").append(cur).append(",\"max\":").append(max).append('}');
        }
        return b.append('}').toString();
    }

    // ---------- calls ----------
    private static final Set<String> EMERGENCY = new HashSet<String>();
    static { for (String s : new String[] { "911", "112", "999", "000", "110", "119", "933" }) EMERGENCY.add(s); }

    private static String cleanNumber(String n) {
        StringBuilder b = new StringBuilder();
        for (int i = 0; i < n.length(); i++) { char c = n.charAt(i); if ((c >= '0' && c <= '9') || c == '+' || c == '*' || c == '#' || c == ',' || c == ';') b.append(c); }
        return b.toString();
    }

    private String call(String number) {
        String n = cleanNumber(number);
        if (n.length() < 3) return err("that is not a phone number");
        String digits = n.replaceAll("[^0-9]", "");
        if (EMERGENCY.contains(digits)) return err("Jade does not place emergency calls. Dial that yourself.");
        return start(new Intent("android.intent.action.CALL", Uri.parse("tel:" + n.replace("#", "%23"))));
    }

    private String answer() {
        if (Build.VERSION.SDK_INT < 26) return err("answering calls needs Android 8 or newer");
        try { ((TelecomManager) a.getSystemService("telecom")).acceptRingingCall(); return OK; } catch (Throwable e) { return err("answer: " + e.getMessage()); }
    }

    private String hangup() {
        if (Build.VERSION.SDK_INT < 28) return err("hanging up from the app needs Android 9 or newer");
        try { boolean ok = ((TelecomManager) a.getSystemService("telecom")).endCall(); return ok ? OK : err("there was no call to end"); } catch (Throwable e) { return err("hangup: " + e.getMessage()); }
    }

    private String speaker(boolean on) {
        AudioManager am = (AudioManager) a.getSystemService("audio");
        am.setSpeakerphoneOn(on);
        return "{\"ok\":true,\"speaker\":" + am.isSpeakerphoneOn() + "}";
    }

    private String callState() {
        int s = ((TelephonyManager) a.getSystemService("phone")).getCallState();
        return "{\"state\":\"" + (s == 1 ? "ringing" : s == 2 ? "in a call" : "idle") + "\"}";
    }

    private String callLog(int limit) {
        Cursor c = a.getContentResolver().query(Uri.parse("content://call_log/calls"), new String[] { "number", "type", "date", "duration", "name", "new" }, null, null, "date DESC");
        if (c == null) return err("the call log is not available");
        StringBuilder b = new StringBuilder("[");
        try {
            int n = 0, lim = Math.max(1, Math.min(50, limit));
            String[] kind = { "", "incoming", "outgoing", "missed", "voicemail", "rejected", "blocked" };
            while (n < lim && c.moveToNext()) {
                int t = c.getInt(c.getColumnIndex("type"));
                if (n++ > 0) b.append(',');
                b.append("{\"number\":").append(World.json(String.valueOf(c.getString(c.getColumnIndex("number"))))).append(",\"name\":").append(World.json(String.valueOf(c.getString(c.getColumnIndex("name") ) == null ? "" : c.getString(c.getColumnIndex("name")))))
                    .append(",\"type\":").append(World.json(t >= 0 && t < kind.length ? kind[t] : "other")).append(",\"date\":").append(c.getLong(c.getColumnIndex("date"))).append(",\"seconds\":").append(c.getLong(c.getColumnIndex("duration"))).append(",\"unseen\":").append(c.getInt(c.getColumnIndex("new")) == 1).append('}');
            }
        } finally { c.close(); }
        return b.append(']').toString();
    }

    // ---------- texts, contacts ----------
    private String smsSend(String to, String text) {
        String n = cleanNumber(to);
        if (n.length() < 3) return err("that is not a phone number");
        if (text.length() == 0) return err("text is empty");
        if (text.length() > 1000) return err("texts are limited to 1000 characters here");
        SmsManager sm = SmsManager.getDefault();
        ArrayList<String> parts = sm.divideMessage(text);
        sm.sendMultipartTextMessage(n, null, parts, null, null);
        return "{\"ok\":true,\"to\":" + World.json(n) + ",\"parts\":" + parts.size() + ",\"note\":\"handed to the phone's radio; Android gives no delivery receipt here\"}";
    }

    private String smsRead(JSONObject o) {
        String box = o.optString("box", "inbox");
        if (!box.equals("inbox") && !box.equals("sent")) return err("box is inbox or sent");
        List<String> where = new ArrayList<String>(); List<String> args = new ArrayList<String>();
        String from = o.optString("from", ""), has = o.optString("contains", "");
        if (from.length() > 0) { where.add("address LIKE ?"); args.add("%" + cleanNumber(from) + "%"); }
        if (has.length() > 0) { where.add("body LIKE ?"); args.add("%" + has + "%"); }
        if (o.optBoolean("unread", false)) where.add("read = 0");
        StringBuilder sel = new StringBuilder();
        for (int i = 0; i < where.size(); i++) { if (i > 0) sel.append(" AND "); sel.append(where.get(i)); }
        Cursor c = a.getContentResolver().query(Uri.parse("content://sms/" + box), new String[] { "address", "body", "date", "read" }, sel.length() == 0 ? null : sel.toString(), args.isEmpty() ? null : args.toArray(new String[0]), "date DESC");
        if (c == null) return err("texts are not readable on this phone");
        StringBuilder b = new StringBuilder("[");
        try {
            int n = 0, lim = Math.max(1, Math.min(50, o.optInt("limit", 10)));
            while (n < lim && c.moveToNext()) {
                String body = c.getString(c.getColumnIndex("body")); if (body == null) body = ""; if (body.length() > 2000) body = body.substring(0, 2000) + "…";
                if (n++ > 0) b.append(',');
                b.append("{\"from\":").append(World.json(String.valueOf(c.getString(c.getColumnIndex("address"))))).append(",\"text\":").append(World.json(body))
                    .append(",\"date\":").append(c.getLong(c.getColumnIndex("date"))).append(",\"read\":").append(c.getInt(c.getColumnIndex("read")) == 1).append('}');
            }
        } finally { c.close(); }
        return b.append(']').toString();
    }

    private String contacts(String q, int limit) {
        Cursor c = a.getContentResolver().query(Uri.parse("content://com.android.contacts/data/phones"), new String[] { "display_name", "data1" },
            q.length() == 0 ? null : "display_name LIKE ?", q.length() == 0 ? null : new String[] { "%" + q + "%" }, "display_name ASC");
        if (c == null) return err("contacts are not available");
        StringBuilder b = new StringBuilder("[");
        Set<String> seen = new HashSet<String>();
        try {
            int n = 0, lim = Math.max(1, Math.min(50, limit));
            while (n < lim && c.moveToNext()) {
                String name = c.getString(c.getColumnIndex("display_name")), num = c.getString(c.getColumnIndex("data1"));
                if (num == null || !seen.add(name + "|" + cleanNumber(num))) continue;
                if (n++ > 0) b.append(',');
                b.append("{\"name\":").append(World.json(String.valueOf(name))).append(",\"number\":").append(World.json(num)).append('}');
            }
        } finally { c.close(); }
        return b.append(']').toString();
    }

    // ---------- clipboard, links, apps, screen ----------
    private String clipGet() {
        return ui(new Callable<String>() { public String call() {
            ClipboardManager cm = (ClipboardManager) a.getSystemService("clipboard");
            if (!cm.hasPrimaryClip() || cm.getPrimaryClip().getItemCount() == 0) return "{\"text\":\"\"}";
            CharSequence t = cm.getPrimaryClip().getItemAt(0).getText();
            return "{\"text\":" + World.json(t == null ? "" : t.toString()) + "}";
        } });
    }

    private String clipSet(final String text) {
        return ui(new Callable<String>() { public String call() { ((ClipboardManager) a.getSystemService("clipboard")).setPrimaryClip(ClipData.newPlainText("Jade", text)); return OK; } });
    }

    private String openUrl(String url) {
        String l = url.toLowerCase();
        if (!(l.startsWith("http://") || l.startsWith("https://") || l.startsWith("geo:") || l.startsWith("tel:") || l.startsWith("mailto:") || l.startsWith("sms:") || l.startsWith("smsto:") || l.startsWith("market:")))
            return err("that kind of address is not allowed (http, https, geo, tel, mailto, sms)");
        return start(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
    }

    private String apps(String q) {
        PackageManager pm = a.getPackageManager();
        Intent main = new Intent(Intent.ACTION_MAIN);
        main.addCategory(Intent.CATEGORY_LAUNCHER);
        StringBuilder b = new StringBuilder("[");
        int n = 0; String lq = q.toLowerCase();
        for (ResolveInfo r : pm.queryIntentActivities(main, 0)) {
            String label = String.valueOf(r.loadLabel(pm)), pkg = r.activityInfo.packageName;
            if (lq.length() > 0 && !label.toLowerCase().contains(lq) && !pkg.toLowerCase().contains(lq)) continue;
            if (n++ >= 60) break;
            if (n > 1) b.append(',');
            b.append("{\"name\":").append(World.json(label)).append(",\"package\":").append(World.json(pkg)).append('}');
        }
        return b.append(']').toString();
    }

    private String appLaunch(String pkg) {
        Intent i = a.getPackageManager().getLaunchIntentForPackage(pkg);
        if (i == null) return err("no app with the package " + pkg);
        return start(i);
    }

    /** Screen brightness for the whole phone: needs "Modify system settings" for Jade Link (opens that screen if not). */
    private String brightness(final double level) {
        if (Build.VERSION.SDK_INT >= 23 && !Settings.System.canWrite(a)) {
            start(new Intent("android.settings.action.MANAGE_WRITE_SETTINGS", Uri.parse("package:" + a.getPackageName())));
            return err("Jade Link needs \"Modify system settings\" to change brightness: that screen is open on the phone for the user to allow it");
        }
        android.content.ContentResolver cr = a.getContentResolver();
        if (level < 0) { Settings.System.putInt(cr, "screen_brightness_mode", 1); return "{\"ok\":true,\"brightness\":\"automatic\"}"; }
        double lv = Math.max(0.01, Math.min(1, level));
        Settings.System.putInt(cr, "screen_brightness_mode", 0);
        Settings.System.putInt(cr, "screen_brightness", (int) Math.round(lv * 255));
        return "{\"ok\":true,\"brightness\":" + lv + "}";
    }

    private android.os.PowerManager.WakeLock awake;
    /** Keep the screen on (up to an hour at a time; off again with on:false). */
    @SuppressWarnings("deprecation")
    private synchronized String keepAwake(final boolean on) {
        try {
            if (awake != null && awake.isHeld()) awake.release();
            awake = null;
            if (on) {
                android.os.PowerManager pm = (android.os.PowerManager) a.getSystemService("power");
                awake = pm.newWakeLock(10 /* SCREEN_BRIGHT_WAKE_LOCK */ | 0x10000000 /* ACQUIRE_CAUSES_WAKEUP */, "jadelink:awake");
                awake.acquire(60L * 60 * 1000);
            }
            return "{\"ok\":true,\"keepAwake\":" + on + (on ? ",\"note\":\"for up to an hour\"" : "") + "}";
        } catch (Throwable e) { return err("keep_awake: " + e.getMessage()); }
    }

    // ---------- radios ----------
    private static String netType(int t) {
        switch (t) {
            case 1: return "GPRS"; case 2: return "EDGE"; case 3: return "UMTS (3G)"; case 4: return "CDMA"; case 5: case 6: case 12: case 14: return "EVDO (3G)";
            case 8: return "HSDPA"; case 9: return "HSUPA"; case 10: return "HSPA"; case 13: return "LTE (4G)"; case 15: return "HSPA+"; case 20: return "5G NR"; case 0: return "unknown";
            default: return "type " + t;
        }
    }

    private String radio() {
        TelephonyManager tm = (TelephonyManager) a.getSystemService("phone");
        StringBuilder b = new StringBuilder("{");
        b.append("\"operator\":").append(World.json(String.valueOf(tm.getNetworkOperatorName()))).append(",\"network\":").append(World.json(netType(Build.VERSION.SDK_INT >= 24 ? tm.getDataNetworkType() : tm.getNetworkType())));
        b.append(",\"roaming\":").append(tm.isNetworkRoaming());
        int sim = tm.getSimState(); String[] sims = { "unknown", "absent", "pin required", "puk required", "network locked", "ready", "not ready", "permanently disabled", "card error", "restricted" };
        b.append(",\"sim\":").append(World.json(sim >= 0 && sim < sims.length ? sims[sim] : "unknown"));
        b.append(",\"airplaneMode\":").append(Settings.Global.getInt(a.getContentResolver(), "airplane_mode_on", 0) == 1);
        if (Build.VERSION.SDK_INT >= 28) {
            SignalStrength ss = tm.getSignalStrength();
            if (ss != null) {
                b.append(",\"signalLevel\":").append(ss.getLevel()).append(",\"signalLevelOf\":4");
                List<CellSignalStrength> l = ss.getCellSignalStrengths();
                if (l != null && !l.isEmpty()) b.append(",\"dbm\":").append(l.get(0).getDbm());
            }
        }
        b.append(",\"fm\":\"Android gives apps no access to the phone's FM tuner; internet radio streams can be played instead\"}");
        return b.toString();
    }

    private static String btState(int bond) { return bond == 12 ? "paired" : bond == 11 ? "pairing" : "not paired"; }

    private static String devJson(BluetoothDevice d, Boolean connected) {
        StringBuilder b = new StringBuilder("{\"name\":").append(World.json(String.valueOf(d.getName()))).append(",\"address\":").append(World.json(d.getAddress())).append(",\"bond\":").append(World.json(btState(d.getBondState())));
        try { int cls = d.getBluetoothClass() == null ? 0 : d.getBluetoothClass().getMajorDeviceClass(); b.append(",\"kind\":").append(World.json(majorClass(cls))); } catch (Throwable ignored) { }
        if (connected != null) b.append(",\"connected\":").append(connected);
        return b.append('}').toString();
    }

    private static String majorClass(int c) {
        switch (c) { case 0x0400: return "audio/video"; case 0x0100: return "computer"; case 0x0200: return "phone"; case 0x0500: return "keyboard/mouse"; case 0x0600: return "imaging"; case 0x0700: return "wearable"; case 0x0800: return "toy"; case 0x0900: return "health"; default: return "other"; }
    }

    private String bluetooth() {
        BluetoothAdapter ad = BluetoothAdapter.getDefaultAdapter();
        if (ad == null) return err("this phone has no Bluetooth");
        StringBuilder b = new StringBuilder("{\"enabled\":" + ad.isEnabled() + ",\"paired\":[");
        int n = 0;
        try { for (BluetoothDevice d : ad.getBondedDevices()) { if (n++ > 0) b.append(','); b.append(devJson(d, isConnected(d))); } } catch (SecurityException e) { return err("Bluetooth permission (nearby devices) was not granted"); }
        return b.append("]}").toString();
    }

    /** Whether a bonded device is actually connected right now (ACL), via the hidden isConnected method. */
    private static Boolean isConnected(BluetoothDevice d) {
        try { return (Boolean) BluetoothDevice.class.getMethod("isConnected").invoke(d); } catch (Throwable e) { return null; }
    }

    private String btPower(boolean on) {
        BluetoothAdapter ad = BluetoothAdapter.getDefaultAdapter();
        if (ad == null) return err("this phone has no Bluetooth");
        if (Build.VERSION.SDK_INT >= 31) {   // Android 12+ forbids enable()/disable(); send the user to the toggle
            if (ad.isEnabled() == on) return "{\"ok\":true,\"enabled\":" + on + "}";
            return start(new Intent(on ? "android.bluetooth.adapter.action.REQUEST_ENABLE" : "android.settings.BLUETOOTH_SETTINGS"));
        }
        try { boolean ok = on ? ad.enable() : ad.disable(); return "{\"ok\":" + ok + ",\"enabled\":" + ad.isEnabled() + ",\"note\":\"takes a moment to come up\"}"; } catch (Throwable e) { return err("bt_power: " + e.getMessage()); }
    }

    private String btScan(int seconds) {
        final BluetoothAdapter ad = BluetoothAdapter.getDefaultAdapter();
        if (ad == null) return err("this phone has no Bluetooth");
        if (!ad.isEnabled()) return err("Bluetooth is off; turn it on first (bt_power on)");
        final java.util.LinkedHashMap<String, BluetoothDevice> found = new java.util.LinkedHashMap<String, BluetoothDevice>();
        final CountDownLatch done = new CountDownLatch(1);
        final android.content.BroadcastReceiver rx = new android.content.BroadcastReceiver() {
            @Override public void onReceive(android.content.Context c, Intent i) {
                String act = i.getAction();
                if (BluetoothDevice.ACTION_FOUND.equals(act)) {
                    BluetoothDevice d = (BluetoothDevice) i.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE);
                    if (d != null) found.put(d.getAddress(), d);
                } else if (BluetoothAdapter.ACTION_DISCOVERY_FINISHED.equals(act)) done.countDown();
            }
        };
        android.content.IntentFilter fi = new android.content.IntentFilter();
        fi.addAction(BluetoothDevice.ACTION_FOUND); fi.addAction(BluetoothAdapter.ACTION_DISCOVERY_FINISHED);
        try {
            a.registerReceiver(rx, fi);
            if (ad.isDiscovering()) ad.cancelDiscovery();
            if (!ad.startDiscovery()) { try { a.unregisterReceiver(rx); } catch (Throwable ignored) { } return err("could not start scanning (the location/nearby-devices permission may be off)"); }
            done.await(Math.max(4, Math.min(30, seconds)), TimeUnit.SECONDS);
        } catch (SecurityException e) { return err("Bluetooth scan permission (nearby devices, or location) was not granted");
        } catch (Throwable e) { return err("bt_scan: " + e.getMessage());
        } finally { try { ad.cancelDiscovery(); } catch (Throwable ignored) { } try { a.unregisterReceiver(rx); } catch (Throwable ignored) { } }
        java.util.Set<String> bonded = new HashSet<String>();
        try { for (BluetoothDevice d : ad.getBondedDevices()) bonded.add(d.getAddress()); } catch (Throwable ignored) { }
        StringBuilder b = new StringBuilder("{\"found\":[");
        int n = 0;
        for (BluetoothDevice d : found.values()) { if (n++ > 0) b.append(','); b.append(devJson(d, null)); }
        b.append("]");
        if (n == 0) b.append(",\"note\":\"nothing new in range; devices must be in pairing mode to appear\"");
        return b.append('}').toString();
    }

    private BluetoothDevice findDevice(String address, String name) {
        BluetoothAdapter ad = BluetoothAdapter.getDefaultAdapter();
        if (ad == null) return null;
        if (address != null && BluetoothAdapter.checkBluetoothAddress(address)) { try { return ad.getRemoteDevice(address); } catch (Throwable ignored) { } }
        if (name != null && name.length() > 0) { try { for (BluetoothDevice d : ad.getBondedDevices()) { String dn = d.getName(); if (dn != null && dn.toLowerCase().contains(name.toLowerCase())) return d; } } catch (Throwable ignored) { } }
        return null;
    }

    private String btPair(String address, String name) {
        BluetoothDevice d = findDevice(address, name);
        if (d == null) return err("no such device; run bt_scan and use its address");
        if (d.getBondState() == 12) return "{\"ok\":true,\"already\":true,\"device\":" + devJson(d, isConnected(d)) + "}";
        try { boolean ok = d.createBond(); return ok ? "{\"ok\":true,\"pairing\":true,\"device\":" + devJson(d, null) + ",\"note\":\"a system pairing prompt may appear for the user to confirm\"}" : err("the phone would not start pairing"); }
        catch (Throwable e) { return err("bt_pair: " + e.getMessage()); }
    }

    private String btUnpair(String address, String name) {
        BluetoothDevice d = findDevice(address, name);
        if (d == null) return err("no such paired device");
        try { Object r = BluetoothDevice.class.getMethod("removeBond").invoke(d); return Boolean.TRUE.equals(r) ? OK : err("could not remove the pairing"); }
        catch (Throwable e) { return err("bt_unpair: " + e.getMessage()); }
    }

    /** Connect or disconnect audio profiles (A2DP for music, Headset for calls) via the hidden profile methods. */
    private String btConnect(String address, String name, final boolean connect) {
        final BluetoothDevice d = findDevice(address, name);
        if (d == null) return err("no such device; pair it first (bt_pair)");
        if (d.getBondState() != 12) return err("pair with it first (bt_pair)");
        final BluetoothAdapter ad = BluetoothAdapter.getDefaultAdapter();
        final int[] profiles = { 2 /* A2DP */, 1 /* HEADSET */ };
        final StringBuilder note = new StringBuilder();
        boolean any = false;
        for (final int prof : profiles) {
            final CountDownLatch got = new CountDownLatch(1);
            final boolean[] ran = { false };
            try {
                ad.getProfileProxy(a, new android.bluetooth.BluetoothProfile.ServiceListener() {
                    @Override public void onServiceConnected(int p, android.bluetooth.BluetoothProfile proxy) {
                        try { ran[0] = (Boolean) proxy.getClass().getMethod(connect ? "connect" : "disconnect", BluetoothDevice.class).invoke(proxy, d); }
                        catch (Throwable ignored) { }
                        finally { try { ad.closeProfileProxy(p, proxy); } catch (Throwable ignored) { } got.countDown(); }
                    }
                    @Override public void onServiceDisconnected(int p) { got.countDown(); }
                }, prof);
                got.await(4, TimeUnit.SECONDS);
            } catch (Throwable ignored) { }
            if (ran[0]) any = true;
        }
        if (!any) return err((connect ? "could not connect to " : "could not disconnect ") + String.valueOf(d.getName()) + " (the device may be off or out of range)");
        return "{\"ok\":true,\"" + (connect ? "connecting" : "disconnecting") + "\":true,\"device\":" + World.json(String.valueOf(d.getName())) + ",\"note\":\"takes a second; check with bluetooth\"}";
    }

    // ---------- media control (whatever app is playing: music, podcasts, radio, video) ----------
    private static final java.util.Map<String, Integer> MEDIA_KEYS = new java.util.HashMap<String, Integer>();
    static {
        MEDIA_KEYS.put("play_pause", 85); MEDIA_KEYS.put("play", 126); MEDIA_KEYS.put("pause", 127);
        MEDIA_KEYS.put("next", 87); MEDIA_KEYS.put("previous", 88); MEDIA_KEYS.put("prev", 88);
        MEDIA_KEYS.put("stop", 86); MEDIA_KEYS.put("fast_forward", 90); MEDIA_KEYS.put("rewind", 89);
    }

    private String mediaKey(String command) {
        Integer code = MEDIA_KEYS.get(command == null ? "" : command.toLowerCase());
        if (code == null) return err("command is one of " + MEDIA_KEYS.keySet());
        AudioManager am = (AudioManager) a.getSystemService("audio");
        long now = android.os.SystemClock.uptimeMillis();
        try {
            am.dispatchMediaKeyEvent(new android.view.KeyEvent(now, now, android.view.KeyEvent.ACTION_DOWN, code, 0));
            am.dispatchMediaKeyEvent(new android.view.KeyEvent(now, now, android.view.KeyEvent.ACTION_UP, code, 0));
        } catch (Throwable e) { return err("media: " + e.getMessage()); }
        return "{\"ok\":true,\"command\":" + World.json(command) + ",\"playing\":" + am.isMusicActive() + "}";
    }

    private String nowPlaying() {
        AudioManager am = (AudioManager) a.getSystemService("audio");
        boolean playing = am.isMusicActive();
        return "{\"playing\":" + playing + ",\"note\":" + World.json(playing ? "something is playing through the phone; use media to control it" : "nothing is playing right now") + "}";
    }

    void destroy() { ringStop(); sounds.stop(); keepAwake(false); }
}
