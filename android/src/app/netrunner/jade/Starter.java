package app.netrunner.jade;

import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.provider.Settings;

/**
 * Opening things on the phone from the background. Android 10+ blocks a background app from starting activities unless
 * it may "display over other apps" or has an accessibility service switched on; through the accessibility service it
 * works even then, so that is tried first.
 */
final class Starter {
    private Starter() { }

    static String start(Context c, Intent i) {
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        Context use = JadeAccess.get() != null ? JadeAccess.get() : c;
        try { use.startActivity(i); }
        catch (Exception e) { return "{\"error\":" + MiniJson.quote("no app on this phone can do that (" + e.getMessage() + ")") + "}"; }
        if (!canStart(c)) return "{\"ok\":true,\"note\":\"if nothing opened on the phone, Android blocked it: allow Jade Link to display over other apps, or switch on screen control (Jade Link setup)\"}";
        return "{\"ok\":true}";
    }

    /** Can this app open screens from the background? */
    static boolean canStart(Context c) {
        if (Build.VERSION.SDK_INT < 29) return true;
        if (JadeAccess.isRunning()) return true;
        try { return Settings.canDrawOverlays(c); } catch (Throwable e) { return false; }
    }
}
