package app.netrunner.jade;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** Bring the link back after the phone restarts or the app is updated (if it was on). */
public class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context c, Intent i) {
        String a = i.getAction();
        if ("android.intent.action.BOOT_COMPLETED".equals(a) || "android.intent.action.MY_PACKAGE_REPLACED".equals(a)) LinkService.startIfWanted(c);
    }
}
