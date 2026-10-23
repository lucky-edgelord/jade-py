package app.netrunner.jade;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;
import android.os.VibrationEffect;
import android.os.Vibrator;
import android.provider.Settings;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Sentry's hub, one per process. Every source of events (the foreground service's sensors, phone calls, texts,
 * the notification listener, the page, other services) reports here; the SentryEngine decides which rules fire.
 * A fire does its native alert at once (notification, vibration) and, if the rule names a flow, joins a small
 * persisted queue and goes to jade-py over the link (Jade runs the flow there and acknowledges it). Whatever jade-py
 * has not acknowledged is handed over again when it reconnects (sentry.pending), and dropped if it is older than the
 * rule's max_age_s by then, because acting on stale sensor data is worse than not acting.
 */
final class SentryCore {
    static final String ALERT_CHANNEL = "jade-sentry-alert";
    static final SentryEngine engine = new SentryEngine(new SentryEngine.Clock() { public long now() { return System.currentTimeMillis(); } });

    static Context app;
    static boolean enabled, awake = true;
    static String rulesJson = "";
    private static final List<Map<String, Object>> queue = new ArrayList<Map<String, Object>>();
    private static final List<Object> expired = new ArrayList<Object>();
    private static boolean inited;
    private static int notifyId = 7100;

    private SentryCore() { }

    static synchronized void init(Context c) {
        if (inited) return;
        inited = true; app = c.getApplicationContext();
        SharedPreferences p = app.getSharedPreferences("jade", 0);
        rulesJson = p.getString("sentry_rules", "");
        loadQueue(p.getString("sentry_queue", "[]"));
        if (rulesJson.length() > 0) applyRules(rulesJson);
    }

    /** The app came up: if Sentry was on, make sure its service is too (this runs with the app on screen, which Android requires for camera and microphone). */
    static void resume(Context c) {
        if (enabled && !engine.sources().isEmpty() && SentryService.inst == null) {
            try { Intent i = new Intent(app, SentryService.class); if (Build.VERSION.SDK_INT >= 26) app.startForegroundService(i); else app.startService(i); } catch (Throwable ignored) { }
        }
    }

    @SuppressWarnings("unchecked")
    private static void applyRules(String json) {
        try { Map<String, Object> root = MiniJson.obj(json); enabled = MiniJson.bool(root, "enabled", true); awake = MiniJson.bool(root, "awake", true); } catch (Exception e) { enabled = false; }
        engine.configure(json);
    }

    /** From the page: the full, normalised rules. Returns {"errors":[…],"sources":[…],"running":bool}. */
    static synchronized String configure(Context c, String json) {
        init(c);
        List<String> errs = engine.configure(json);
        rulesJson = json; applyRules(json);
        app.getSharedPreferences("jade", 0).edit().putString("sentry_rules", json).apply();
        boolean want = enabled && !engine.sources().isEmpty();
        try { Intent i = new Intent(app, SentryService.class); if (want) { if (Build.VERSION.SDK_INT >= 26) app.startForegroundService(i); else app.startService(i); } else { app.stopService(i); } } catch (Exception e) { errs.add("could not start the service: " + e.getMessage()); }
        SentryService s = SentryService.inst; if (s != null && want) s.refresh();
        Map<String, Object> r = new LinkedHashMap<String, Object>();
        r.put("errors", new ArrayList<Object>(errs)); r.put("sources", new ArrayList<Object>(engine.sources())); r.put("running", want);
        return MiniJson.write(r);
    }

    static boolean wants(String name) { return enabled && engine.sources().contains("event:" + name); }

    // ---------- which systems are switched on, and their tuning, from the rules' "systems" and "settings" ----------
    /** A system is on if the rules say so; if they do not say, everything that is not sensitive is on and everything sensitive is off. */
    static synchronized Set<String> enabledSystems() {
        Set<String> out = new LinkedHashSet<String>();
        Map<String, Object> sys = null;
        try { sys = MiniJson.m(MiniJson.obj(rulesJson.length() == 0 ? "{}" : rulesJson), "systems"); } catch (Exception ignored) { }
        for (SentrySystems.Info i : SentrySystems.ALL.values()) {
            Object v = sys == null ? null : sys.get(i.id);
            if (v instanceof Boolean ? (Boolean) v : !i.sensitive) out.add(i.id);
        }
        return out;
    }

    static double settingNum(String key, double dflt) {
        try { return MiniJson.num(MiniJson.m(MiniJson.obj(rulesJson.length() == 0 ? "{}" : rulesJson), "settings"), key, dflt); } catch (Exception e) { return dflt; }
    }

    /** Does any enabled rule want a camera picture kept when it fires? (Otherwise the camera never keeps pictures.) */
    @SuppressWarnings("unchecked")
    static boolean wantFrames() {
        try { List<Object> l = MiniJson.l(MiniJson.obj(rulesJson.length() == 0 ? "{}" : rulesJson), "triggers"); if (l != null) for (Object o : l) if (MiniJson.bool(o, "snapshot", false) && MiniJson.bool(o, "enabled", true)) return true; } catch (Exception ignored) { }
        return false;
    }

    // ---------- sources that live outside the service ----------
    private static final android.os.Handler ui = new android.os.Handler(android.os.Looper.getMainLooper());
    private static int keyPresses; private static String keyName = ""; private static final Runnable keyFlush = new Runnable() { public void run() {
        Map<String, Object> d = new HashMap<String, Object>(); d.put("key", keyName); d.put("presses", (double) keyPresses); keyPresses = 0; emit("volume_key", d); } };

    /** A volume button went down (from the accessibility service). Presses within 700 ms of each other are counted as one gesture. */
    static void volumeKey(boolean up) {
        if (!wants("volume_key")) return;
        String n = up ? "up" : "down";
        ui.removeCallbacks(keyFlush);
        if (!n.equals(keyName)) keyPresses = 0;
        keyName = n; keyPresses++;
        ui.postDelayed(keyFlush, 700);
    }

    private static String lastApp = "";
    /** The app in front changed (from the accessibility service). */
    static void appFront(String pkg, String label) {
        touch();
        if (!enabled || pkg == null || pkg.equals(lastApp)) return;
        lastApp = pkg; set("app.foreground", pkg);
        if (wants("app")) { Map<String, Object> d = new HashMap<String, Object>(); d.put("package", pkg); d.put("label", label == null ? pkg : label); emit("app", d); }
    }

    // ---------- inputs ----------
    static void emit(String name, Map<String, Object> data) { if (!enabled) return; handle(engine.event(name, data)); }
    static void set(Map<String, Object> readings) { if (!enabled) return; handle(engine.update(readings)); }
    static void set(String k, Object v) { Map<String, Object> m = new HashMap<String, Object>(); m.put(k, v); set(m); }
    static void tick() { if (enabled) handle(engine.tick()); }
    static void touch() { engine.touch(); }

    // ---------- fires ----------
    @SuppressWarnings("unchecked")
    private static void handle(List<SentryEngine.Fire> fires) {
        for (SentryEngine.Fire f : fires) {
            Map<String, Object> rule = f.rule;
            String mode = MiniJson.str(rule, "mode");
            Map<String, Object> alert = MiniJson.m(rule, "alert");
            if (alert != null && !"silent".equals(mode)) { try { nativeAlert(f, alert); } catch (Throwable ignored) { } }
            String flow = MiniJson.str(rule, "flow");
            if (flow == null || flow.length() == 0) continue;
            Map<String, Object> q = new LinkedHashMap<String, Object>();
            q.put("qid", f.id + "@" + f.at); q.put("id", f.id); q.put("kind", f.kind); q.put("at", (double) f.at);
            q.put("values", f.values); if (f.event != null) q.put("event", f.event);
            q.put("flow", flow); if (rule.get("input") != null) q.put("input", rule.get("input"));
            if (MiniJson.bool(rule, "snapshot", false)) { SentryService sv = SentryService.inst; String path = sv == null ? null : sv.saveFrame(f.id + "-" + f.at); if (path != null) { q.put("snapshot", path); String b = b64(path); if (b != null) q.put("snapshot_b64", b); } }
            q.put("max_age_s", MiniJson.num(rule, "max_age_s", 600)); q.put("name", MiniJson.str(rule, "name") == null ? f.id : MiniJson.str(rule, "name"));
            synchronized (SentryCore.class) { queue.add(q); while (queue.size() > 50) queue.remove(0); persist(); }
            LinkService.emit("sentry_fire", q);
        }
    }

    private static String b64(String path) {
        try { java.io.File f = new java.io.File(path); byte[] d = new byte[(int) f.length()]; java.io.FileInputStream in = new java.io.FileInputStream(f); try { int o = 0; while (o < d.length) { int n = in.read(d, o, d.length - o); if (n < 0) break; o += n; } } finally { in.close(); }
              return android.util.Base64.encodeToString(d, android.util.Base64.NO_WRAP); } catch (Throwable e) { return null; }
    }

    @SuppressWarnings("unchecked")
    private static void nativeAlert(SentryEngine.Fire f, Map<String, Object> alert) {
        String text = MiniJson.str(alert, "notify");
        if (text != null && text.length() > 0) {
            NotificationManager nm = (NotificationManager) app.getSystemService(Context.NOTIFICATION_SERVICE);
            if (nm != null) {
                Notification.Builder b;
                if (Build.VERSION.SDK_INT >= 26) { nm.createNotificationChannel(new NotificationChannel(ALERT_CHANNEL, "Sentry alerts", NotificationManager.IMPORTANCE_HIGH)); b = new Notification.Builder(app, ALERT_CHANNEL); } else b = new Notification.Builder(app);
                String title = MiniJson.str(f.rule, "name"); if (title == null) title = f.id;
                b.setSmallIcon(app.getApplicationInfo().icon).setContentTitle("Sentry: " + title).setContentText(text).setColor(0xff1fe5a5).setAutoCancel(true)
                    .setContentIntent(PendingIntent.getActivity(app, 0, new Intent(app, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP), PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT));
                nm.notify(notifyId++, b.build());
            }
        }
        List<Object> vib = MiniJson.l(alert, "vibrate");
        if (vib != null && !vib.isEmpty()) {
            long[] p = new long[Math.min(vib.size(), 40) + 1]; long total = 0;
            for (int i = 0; i < p.length - 1; i++) { long v = Math.max(0, Math.min(5000, (long) ((Number) vib.get(i)).doubleValue())); if (total + v > 30000) v = Math.max(0, 30000 - total); total += v; p[i + 1] = v; }
            Vibrator v = (Vibrator) app.getSystemService(Context.VIBRATOR_SERVICE);
            if (v != null && v.hasVibrator()) { if (Build.VERSION.SDK_INT >= 26) v.vibrate(VibrationEffect.createWaveform(p, -1)); else v.vibrate(p, -1); }
        }
    }

    // ---------- the queue jade-py drains ----------
    /** jade-py (re)connected: hand it what is waiting. Events older than their max_age_s are reported as expired, not run. */
    static synchronized String pending() {
        long now = System.currentTimeMillis();
        List<Object> keep = new ArrayList<Object>(), old = new ArrayList<Object>(expired); expired.clear();
        for (int i = queue.size() - 1; i >= 0; i--) {
            Map<String, Object> q = queue.get(i);
            if (now - (long) MiniJson.num(q, "at", 0) > MiniJson.num(q, "max_age_s", 600) * 1000) { old.add(q); queue.remove(i); } else keep.add(0, q);
        }
        persist();
        Map<String, Object> r = new LinkedHashMap<String, Object>(); r.put("items", keep); r.put("expired", old);
        return MiniJson.write(r);
    }

    static synchronized void ack(String qid) {
        for (int i = queue.size() - 1; i >= 0; i--) if (qid.equals(MiniJson.str(queue.get(i), "qid"))) queue.remove(i);
        persist();
    }

    @SuppressWarnings("unchecked")
    private static void loadQueue(String json) {
        try { Object o = MiniJson.parse(json); if (o instanceof List) for (Object x : (List<Object>) o) if (x instanceof Map) queue.add((Map<String, Object>) x); } catch (Exception ignored) { }
    }

    private static void persist() {
        try { app.getSharedPreferences("jade", 0).edit().putString("sentry_queue", MiniJson.write(new ArrayList<Object>(queue))).apply(); } catch (Exception ignored) { }
    }

    // ---------- status ----------
    static boolean notificationAccess() {
        try { String s = Settings.Secure.getString(app.getContentResolver(), "enabled_notification_listeners"); return s != null && s.contains(app.getPackageName()); } catch (Exception e) { return false; }
    }

    @SuppressWarnings("unchecked")
    static synchronized String status(Context c) {
        init(c);
        Map<String, Object> r = MiniJson.obj(engine.status());
        r.put("enabled", enabled); r.put("running", SentryService.inst != null); r.put("sources", new ArrayList<Object>(engine.sources()));
        r.put("notification_access", notificationAccess()); r.put("queued", (double) queue.size());
        SentryService sv = SentryService.inst; if (sv != null) r.put("systems", sv.systemsStatus());
        return MiniJson.write(r);
    }
}
