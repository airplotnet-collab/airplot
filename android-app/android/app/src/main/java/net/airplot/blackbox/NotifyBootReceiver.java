package net.airplot.blackbox;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

// Restarts the notification centre after the phone restarts or the app is updated, if it was on.
public class NotifyBootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context ctx, Intent intent) {
        String a = intent.getAction();
        if (!Intent.ACTION_BOOT_COMPLETED.equals(a) && !Intent.ACTION_MY_PACKAGE_REPLACED.equals(a)) return;
        if (!NotifyPrefs.get(ctx).getBoolean(NotifyPrefs.K_ENABLED, false)) return;
        try { NotifyCentreService.start(ctx); } catch (Exception e) { /* Android refused a background start - opening the app starts it */ }
    }
}
