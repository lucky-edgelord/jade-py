package app.netrunner.jade;

import android.content.Context;
import android.content.Intent;
import android.os.Build;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/** Android runtime permissions: what is granted, and asking for more (only an activity can ask, so MainActivity opens to do it). */
final class Perms {
    private Perms() { }

    static final String[] SENSES = { "android.permission.CAMERA", "android.permission.RECORD_AUDIO", "android.permission.ACCESS_FINE_LOCATION", "android.permission.ACTIVITY_RECOGNITION" };
    static final String[] PHONE = { "android.permission.CALL_PHONE", "android.permission.ANSWER_PHONE_CALLS", "android.permission.READ_PHONE_STATE", "android.permission.READ_CALL_LOG",
        "android.permission.SEND_SMS", "android.permission.READ_SMS", "android.permission.RECEIVE_SMS", "android.permission.READ_CONTACTS" };
    static final String[] BT = { "android.permission.BLUETOOTH_CONNECT", "android.permission.BLUETOOTH_SCAN" };
    static final String[] NOTIFY = { "android.permission.POST_NOTIFICATIONS" };

    static String[] all() {
        List<String> l = new ArrayList<String>();
        for (String[] g : new String[][] { SENSES, PHONE }) for (String p : g) l.add(p);
        if (Build.VERSION.SDK_INT >= 31) for (String p : BT) l.add(p);
        if (Build.VERSION.SDK_INT >= 33) for (String p : NOTIFY) l.add(p);
        return l.toArray(new String[0]);
    }

    static boolean has(Context c, String p) { try { return c.checkSelfPermission(p) == 0; } catch (Throwable e) { return false; } }

    static Map<String, Object> map(Context c, String[] perms) {
        Map<String, Object> m = new LinkedHashMap<String, Object>();
        for (String p : perms) m.put(p, has(c, p));
        return m;
    }

    static String status(Context c, String[] perms) { return MiniJson.write(map(c, perms)); }

    static volatile CountDownLatch waiting;

    /** Ask for the missing ones (MainActivity opens and shows Android's prompts), wait for the answer, return the status. */
    static String request(Context c, String[] perms, int timeoutS) {
        List<String> need = new ArrayList<String>();
        for (String p : perms) if (!has(c, p)) need.add(p);
        if (need.isEmpty()) return status(c, perms);
        CountDownLatch l = new CountDownLatch(1); waiting = l;
        Intent i = new Intent(c, MainActivity.class).putExtra("perms", join(need)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        String r = Starter.start(c, i);
        if (r.contains("\"error\"")) return r;
        try { l.await(Math.max(10, Math.min(300, timeoutS)), TimeUnit.SECONDS); } catch (InterruptedException ignored) { }
        return status(c, perms);
    }

    static void answered() { CountDownLatch l = waiting; if (l != null) l.countDown(); }

    static String join(List<String> l) { StringBuilder b = new StringBuilder(); for (String s : l) { if (b.length() > 0) b.append(','); b.append(s); } return b.toString(); }
}
