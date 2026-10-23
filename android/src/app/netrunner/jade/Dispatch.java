package app.netrunner.jade;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * What jade-py can ask the phone: one op and its arguments in, one JSON answer out.
 *   info · phone (PhoneHost, and cast_* in CastHost) · screen (JadeAccess) · senses (Senses) · ask · notify · perms
 *   sentry.configure / status / pending / ack / emit / set / systems (SentryCore)
 */
final class Dispatch implements Link.Handler, Link.Info {
    static final int SCREEN_NOTE = 7201;
    private final Context c;
    final PhoneHost phone;
    final CastHost cast;
    final Senses senses;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final Runnable screenOff = new Runnable() { public void run() { try { ((NotificationManager) c.getSystemService(Context.NOTIFICATION_SERVICE)).cancel(SCREEN_NOTE); } catch (Throwable ignored) { } } };

    Dispatch(Context c) { this.c = c; phone = new PhoneHost(c); cast = new CastHost(c); senses = new Senses(c); SentryCore.init(c); }

    private static String err(String m) { return "{\"error\":" + MiniJson.quote(m) + "}"; }

    public String run(String op, String argsJson) {
        try {
            JSONObject a = new JSONObject(argsJson == null || argsJson.length() == 0 ? "{}" : argsJson);
            if (op.equals("info")) return MiniJson.write(info());
            if (op.equals("phone")) {
                String act = a.optString("action", "");
                JSONObject rest = without(a, "action");
                if (act.startsWith("cast_")) return cast.run(act, rest.toString());
                return phone.run(act, rest.toString());
            }
            if (op.equals("screen")) return screen(a);
            if (op.equals("senses")) return senses.run(a);
            if (op.equals("ask")) return Asker.ask(c, a);
            if (op.equals("notify")) return Asker.notify(c, a);
            if (op.equals("perms")) {
                JSONArray p = a.optJSONArray("perms");
                String[] perms = p == null ? Perms.all() : new String[p.length()];
                if (p != null) for (int i = 0; i < p.length(); i++) perms[i] = p.getString(i);
                return a.optBoolean("request", false) ? Perms.request(c, perms, a.optInt("timeout", 120)) : Perms.status(c, perms);
            }
            if (op.equals("sentry.configure")) { Object r = a.opt("rules"); return SentryCore.configure(c, r == null ? "{}" : r.toString()); }
            if (op.equals("sentry.status")) return SentryCore.status(c);
            if (op.equals("sentry.systems")) return SentrySystems.catalogue();
            if (op.equals("sentry.pending")) return SentryCore.pending();
            if (op.equals("sentry.ack")) { SentryCore.ack(a.optString("qid", "")); return "{\"ok\":true}"; }
            if (op.equals("sentry.emit")) { Object d = a.opt("data"); SentryCore.emit(a.optString("event", "custom"), MiniJson.obj(d == null ? "{}" : d.toString())); return "{\"ok\":true}"; }
            if (op.equals("sentry.set")) { Object v = a.opt("values"); SentryCore.set(MiniJson.obj(v == null ? "{}" : v.toString())); return "{\"ok\":true}"; }
            if (op.equals("ping")) return "{\"ok\":true,\"pong\":" + System.currentTimeMillis() + "}";
            return err("unknown op " + op);
        } catch (Throwable e) { return err(op + ": " + e.getMessage()); }
    }

    private static JSONObject without(JSONObject o, String key) throws Exception {
        JSONObject r = new JSONObject();
        Iterator<String> it = o.keys();
        while (it.hasNext()) { String k = it.next(); if (!k.equals(key)) r.put(k, o.get(k)); }
        return r;
    }

    // ---------------------------------------------------------------- screen control
    private String screen(JSONObject a) throws Exception {
        String act = a.optString("action", "");
        if (act.equals("status")) { boolean on = JadeAccess.isRunning(); return "{\"enabled\":" + on + ",\"running\":" + on + ",\"sdk\":" + Build.VERSION.SDK_INT + "}"; }
        if (act.equals("enable") || act.equals("setup")) return Starter.start(c, new Intent("android.settings.ACCESSIBILITY_SETTINGS"));
        JadeAccess svc = JadeAccess.get();
        if (svc == null) return err("screen control is off: the user turns “Jade Link” on in Android's Accessibility settings (screen action enable opens them)");
        indicator();
        return svc.run(act, without(a, "action"));
    }

    /** A standing notice while Jade drives the screen, cleared 20 s after her last action. */
    private void indicator() {
        try {
            NotificationManager nm = (NotificationManager) c.getSystemService(Context.NOTIFICATION_SERVICE);
            Notification.Builder b;
            if (Build.VERSION.SDK_INT >= 26) { nm.createNotificationChannel(new NotificationChannel(Asker.NOTE_CHANNEL, "From Jade", NotificationManager.IMPORTANCE_DEFAULT)); b = new Notification.Builder(c, Asker.NOTE_CHANNEL); } else b = new Notification.Builder(c);
            b.setSmallIcon(c.getApplicationInfo().icon).setContentTitle("Jade is using the screen").setContentText("She reads and taps in your apps. Pull down and switch off Jade Link in Accessibility to stop her.")
                .setOngoing(true).setOnlyAlertOnce(true).setColor(0xff1fe5a5);
            nm.notify(SCREEN_NOTE, b.build());
            main.removeCallbacks(screenOff); main.postDelayed(screenOff, 20000);
        } catch (Throwable ignored) { }
    }

    // ---------------------------------------------------------------- what the phone is and can do
    Map<String, Object> info() {
        Map<String, Object> m = device();
        try { m.put("battery", MiniJson.parse(senses.world.battery())); } catch (Throwable ignored) { }
        m.put("screen_control", JadeAccess.isRunning());
        m.put("notification_access", SentryCore.notificationAccess());
        m.put("can_open_apps", Starter.canStart(c));
        m.put("permissions", Perms.map(c, Perms.all()));
        try { m.put("sentry", MiniJson.parse(SentryCore.status(c))); } catch (Throwable ignored) { }
        return m;
    }

    public Map<String, Object> device() {
        Map<String, Object> m = new LinkedHashMap<String, Object>();
        m.put("app", "jade-link"); m.put("version", LinkService.version(c));
        m.put("manufacturer", Build.MANUFACTURER); m.put("model", Build.MODEL); m.put("android", Build.VERSION.RELEASE); m.put("sdk", (double) Build.VERSION.SDK_INT);
        return m;
    }

    void destroy() { phone.destroy(); senses.destroy(); }
}
