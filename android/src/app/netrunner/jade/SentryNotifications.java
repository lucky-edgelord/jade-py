package app.netrunner.jade;

import android.app.Notification;
import android.service.notification.NotificationListenerService;
import android.service.notification.StatusBarNotification;

import java.util.HashMap;
import java.util.Map;

/**
 * Lets Sentry react to notifications from other apps. Android only binds this once the user grants "notification
 * access" to Jade in Settings (a sideloaded app may first need "Allow restricted settings" in its App info). It does
 * nothing unless a Sentry rule is listening for notification events, and it never reads one into the chat itself:
 * the text goes to the rules engine as data. Ongoing notifications (music players, downloads) and Jade's own are skipped.
 */
public class SentryNotifications extends NotificationListenerService {
    @Override
    public void onNotificationPosted(StatusBarNotification sbn) {
        try {
            SentryCore.init(getApplicationContext());
            if (!SentryCore.wants("notification") || sbn == null) return;
            String pkg = sbn.getPackageName();
            if (pkg == null || pkg.equals(getPackageName())) return;
            Notification n = sbn.getNotification();
            if (n == null || (n.flags & (Notification.FLAG_ONGOING_EVENT | Notification.FLAG_GROUP_SUMMARY)) != 0) return;
            Map<String, Object> d = new HashMap<String, Object>();
            d.put("package", pkg);
            CharSequence title = n.extras == null ? null : n.extras.getCharSequence(Notification.EXTRA_TITLE);
            CharSequence text = n.extras == null ? null : n.extras.getCharSequence(Notification.EXTRA_TEXT);
            d.put("title", clip(title)); d.put("text", clip(text));
            if (n.category != null) d.put("category", n.category);
            int importance = 3;
            try { RankingMap rm = getCurrentRanking(); Ranking rk = new Ranking(); if (rm != null && rm.getRanking(sbn.getKey(), rk)) importance = rk.getImportance(); } catch (Throwable ignored) { }
            d.put("importance", (double) importance);
            SentryCore.emit("notification", d);
        } catch (Throwable ignored) { }
    }

    private static String clip(CharSequence c) { if (c == null) return ""; String s = c.toString(); return s.length() > 500 ? s.substring(0, 500) : s; }
}
