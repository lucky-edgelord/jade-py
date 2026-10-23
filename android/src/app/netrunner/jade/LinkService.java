package app.netrunner.jade;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;

import java.security.SecureRandom;
import java.util.Map;

/**
 * Keeps the link up: a foreground service (so Android does not stop it) holding the Link, its Dispatch and a
 * standing notice that says where it is connected. Settings live in SharedPreferences "link". Started when the app
 * opens and after the phone boots (if it was on). Started from the app on screen, it may use the camera and
 * microphone; after a reboot, Android allows that only once Jade Link has been opened again.
 */
public class LinkService extends Service {
    static final String CHANNEL = "jadelink";
    static final int ID = 7001;
    static volatile LinkService inst;
    static volatile Link link;
    static volatile Dispatch dispatch;
    private PowerManager.WakeLock wake;
    private final android.os.Handler main = new android.os.Handler(android.os.Looper.getMainLooper());
    private final Runnable refresh = new Runnable() { public void run() { notice(); main.postDelayed(this, 30000); } };

    @Override public IBinder onBind(Intent i) { return null; }

    static SharedPreferences prefs(Context c) { return c.getSharedPreferences("link", 0); }

    static Link.Config config(Context c) {
        SharedPreferences p = prefs(c);
        Link.Config k = new Link.Config();
        k.server = p.getString("server", ""); k.token = p.getString("token", "");
        k.deviceId = p.getString("device_id", "");
        if (k.deviceId.length() == 0) { k.deviceId = random(12); p.edit().putString("device_id", k.deviceId).apply(); }
        k.name = p.getString("name", "");
        k.dialOut = p.getBoolean("dial_out", false) && k.server.length() > 0;
        k.listen = p.getBoolean("listen", false);
        k.port = p.getInt("port", 8765);
        k.listenToken = p.getString("listen_token", "");
        if (k.listenToken.length() == 0) { k.listenToken = "jl_" + random(24); p.edit().putString("listen_token", k.listenToken).apply(); }
        k.lan = p.getBoolean("lan", true);
        return k;
    }

    static String random(int n) {
        String a = "abcdefghijkmnopqrstuvwxyz23456789"; SecureRandom r = new SecureRandom(); StringBuilder b = new StringBuilder();
        for (int i = 0; i < n; i++) b.append(a.charAt(r.nextInt(a.length())));
        return b.toString();
    }

    static String version(Context c) { try { return c.getPackageManager().getPackageInfo(c.getPackageName(), 0).versionName; } catch (Throwable e) { return "?"; } }

    static boolean wanted(Context c) { Link.Config k = config(c); return k.dialOut || k.listen; }

    static void startIfWanted(Context c) {
        Context a = c.getApplicationContext();
        Intent i = new Intent(a, LinkService.class);
        try {
            if (wanted(a)) { if (Build.VERSION.SDK_INT >= 26) a.startForegroundService(i); else a.startService(i); }
            else { a.stopService(i); Link l = link; if (l != null) l.stop(); }
        } catch (Throwable ignored) { }
    }

    /** From SentryCore: a fire, for jade-py. */
    static void emit(String type, Object data) { Link l = link; if (l != null) l.emit(type, data); }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        inst = this;
        if (dispatch == null) dispatch = new Dispatch(getApplicationContext());
        if (link == null) link = new Link(dispatch, dispatch);
        notice();
        link.apply(config(this));
        try { if (wake == null) { wake = ((PowerManager) getSystemService(POWER_SERVICE)).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "jadelink:link"); wake.setReferenceCounted(false); } wake.acquire(24L * 60 * 60 * 1000); } catch (Throwable ignored) { }
        main.removeCallbacks(refresh); main.postDelayed(refresh, 30000);
        if (!wanted(this)) { stopSelf(); }
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        main.removeCallbacks(refresh);
        Link l = link; if (l != null) l.stop();
        try { if (wake != null && wake.isHeld()) wake.release(); } catch (Throwable ignored) { }
        if (inst == this) inst = null;
        super.onDestroy();
    }

    @SuppressWarnings("unchecked")
    private void notice() {
        String text = "Starting…";
        Link l = link;
        if (l != null) {
            Map<String, Object> s = l.status();
            Map<String, Object> d = (Map<String, Object>) s.get("dial_out"), ls = (Map<String, Object>) s.get("listen");
            StringBuilder b = new StringBuilder();
            if (Boolean.TRUE.equals(d.get("on"))) b.append(Boolean.TRUE.equals(d.get("connected")) ? "Connected to " + host(String.valueOf(d.get("server"))) : "Reaching " + host(String.valueOf(d.get("server"))) + "…");
            if (Boolean.TRUE.equals(ls.get("on"))) { if (b.length() > 0) b.append(" · "); b.append(Boolean.TRUE.equals(ls.get("listening")) ? "listening on " + MiniJson.str(ls, "port") : "cannot listen"); }
            text = b.length() == 0 ? "Off" : b.toString();
        }
        try {
            NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
            Notification.Builder b;
            if (Build.VERSION.SDK_INT >= 26) { nm.createNotificationChannel(new NotificationChannel(CHANNEL, "Jade Link", NotificationManager.IMPORTANCE_LOW)); b = new Notification.Builder(this, CHANNEL); } else b = new Notification.Builder(this);
            b.setSmallIcon(getApplicationInfo().icon).setContentTitle("Jade Link").setContentText(text).setOngoing(true).setOnlyAlertOnce(true).setShowWhen(false).setColor(0xff1fe5a5)
                .setContentIntent(PendingIntent.getActivity(this, 3, new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP), PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT));
            Notification n = b.build();
            int types = types();
            try {
                if (Build.VERSION.SDK_INT >= 29 && types != 0) startForeground(ID, n, types); else startForeground(ID, n);
            } catch (Throwable e) {
                // after a reboot Android refuses camera / microphone / location for a service the user did not just open: run without them
                startForeground(ID, n, Build.VERSION.SDK_INT >= 34 ? 1073741824 : 1);
            }
        } catch (Throwable ignored) { }
    }

    private static String host(String url) { return url.replaceFirst("(?i)^https?://", "").replaceFirst("/.*$", ""); }

    /** Foreground-service types: only ones whose permission is held (Android 14 refuses the rest). */
    private int types() {
        int t = 0;
        if (Perms.has(this, "android.permission.ACCESS_FINE_LOCATION")) t |= 8;
        if (Perms.has(this, "android.permission.CAMERA")) t |= 64;
        if (Perms.has(this, "android.permission.RECORD_AUDIO")) t |= 128;
        if (Build.VERSION.SDK_INT >= 34) t |= 1073741824;   // specialUse
        else if (Build.VERSION.SDK_INT >= 29) t |= 1;       // dataSync
        return t;
    }
}
