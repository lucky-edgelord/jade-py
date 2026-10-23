package app.netrunner.jade;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** The Allow / Don't buttons on Jade's questions (explicit PendingIntents only: not exported). */
public class AskReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context c, Intent i) {
        String a = i.getAction();
        if (Asker.ACTION.equals(a)) Asker.answer(i.getStringExtra("id"), i.getBooleanExtra("allow", false));
    }
}
