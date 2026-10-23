package app.netrunner.jade;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.os.Build;

import org.json.JSONObject;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/**
 * Asking the person holding the phone: a yes / no that jade-py waits on (calls, texts, turning Sentry on, approvals).
 * It shows as a heads-up notification with Allow and Don't buttons, and in Jade Link's own screen. Nobody answering
 * is a no. Also plain notifications (the phone tool's notify).
 */
final class Asker {
    static final String CHANNEL = "jadelink-ask", NOTE_CHANNEL = "jadelink-notes", ACTION = "app.netrunner.jadelink.ASK";
    static final class Ask { String id, title, detail; long at; final CountDownLatch done = new CountDownLatch(1); volatile Boolean allowed; int nid; }
    static final Map<String, Ask> pending = new LinkedHashMap<String, Ask>();
    private static int seq = 7300, noteSeq = 7600;

    private Asker() { }

    private static NotificationManager nm(Context c) { return (NotificationManager) c.getSystemService(Context.NOTIFICATION_SERVICE); }

    static String ask(Context c, JSONObject o) {
        final Ask a = new Ask();
        a.title = o.optString("title", "Jade is asking"); a.detail = o.optString("detail", ""); a.at = System.currentTimeMillis();
        int timeout = Math.max(10, Math.min(1800, o.optInt("timeout", 600)));
        synchronized (pending) { a.nid = ++seq; a.id = "a" + a.nid + "-" + (a.at % 100000); pending.put(a.id, a); }
        try {
            NotificationManager m = nm(c);
            Notification.Builder b;
            if (Build.VERSION.SDK_INT >= 26) { m.createNotificationChannel(new NotificationChannel(CHANNEL, "Jade is asking", NotificationManager.IMPORTANCE_HIGH)); b = new Notification.Builder(c, CHANNEL); } else b = new Notification.Builder(c);
            b.setSmallIcon(c.getApplicationInfo().icon).setContentTitle(a.title).setContentText(a.detail).setStyle(new Notification.BigTextStyle().bigText(a.detail))
                .setColor(0xff1fe5a5).setOngoing(true).setAutoCancel(false).setPriority(Notification.PRIORITY_HIGH).setCategory(Notification.CATEGORY_CALL)
                .setContentIntent(PendingIntent.getActivity(c, a.nid, new Intent(c, MainActivity.class).putExtra("asks", true).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP), PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT))
                .addAction(0, "Don't", answerIntent(c, a, false))
                .addAction(0, "Allow", answerIntent(c, a, true));
            m.notify(a.nid, b.build());
        } catch (Throwable e) { /* the app screen still lists it */ }
        try { a.done.await(timeout, TimeUnit.SECONDS); } catch (InterruptedException ignored) { }
        synchronized (pending) { pending.remove(a.id); }
        try { nm(c).cancel(a.nid); } catch (Throwable ignored) { }
        boolean yes = Boolean.TRUE.equals(a.allowed);
        return "{\"ok\":true,\"allowed\":" + yes + ",\"answered\":" + (a.allowed != null) + "}";
    }

    private static PendingIntent answerIntent(Context c, Ask a, boolean allow) {
        Intent i = new Intent(c, AskReceiver.class).setAction(ACTION).putExtra("id", a.id).putExtra("allow", allow);
        return PendingIntent.getBroadcast(c, a.nid * 2 + (allow ? 1 : 0), i, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
    }

    static void answer(String id, boolean allow) {
        Ask a; synchronized (pending) { a = pending.get(id); }
        if (a == null || a.allowed != null) return;
        a.allowed = allow; a.done.countDown();
    }

    static String list() {
        List<Object> l = new ArrayList<Object>();
        synchronized (pending) {
            for (Ask a : pending.values()) { Map<String, Object> m = new LinkedHashMap<String, Object>(); m.put("id", a.id); m.put("title", a.title); m.put("detail", a.detail); m.put("at", (double) a.at); l.add(m); }
        }
        return MiniJson.write(l);
    }

    static String notify(Context c, JSONObject o) {
        try {
            NotificationManager m = nm(c);
            Notification.Builder b;
            if (Build.VERSION.SDK_INT >= 26) { m.createNotificationChannel(new NotificationChannel(NOTE_CHANNEL, "From Jade", NotificationManager.IMPORTANCE_DEFAULT)); b = new Notification.Builder(c, NOTE_CHANNEL); } else b = new Notification.Builder(c);
            String body = o.optString("body", "");
            b.setSmallIcon(c.getApplicationInfo().icon).setContentTitle(o.optString("title", "Jade")).setContentText(body).setStyle(new Notification.BigTextStyle().bigText(body))
                .setColor(0xff1fe5a5).setAutoCancel(true)
                .setContentIntent(PendingIntent.getActivity(c, 1, new Intent(c, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP), PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT));
            int id; synchronized (pending) { id = ++noteSeq; }
            m.notify(id, b.build());
            if (Build.VERSION.SDK_INT >= 33 && !Perms.has(c, "android.permission.POST_NOTIFICATIONS")) return "{\"ok\":false,\"error\":\"notifications are not allowed for Jade Link (open the app to allow them)\"}";
            return "{\"ok\":true}";
        } catch (Throwable e) { return "{\"error\":" + MiniJson.quote("notify: " + e.getMessage()) + "}"; }
    }
}
