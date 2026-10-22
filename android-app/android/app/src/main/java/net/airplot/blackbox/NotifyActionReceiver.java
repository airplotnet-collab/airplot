package net.airplot.blackbox;

import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.widget.Toast;
import androidx.core.app.NotificationManagerCompat;

// The shortcut buttons on the notification-centre notifications:
//   on an event:      Snooze <kind> (for the "shortcut snooze" time set in Blackbox), Turn off <kind>
//   on "watching":    Snooze all 1 h / Unsnooze all, Turn off (stops the watcher)
// Each changes the same settings blackbox.html shows, so Settings in the app always matches.
public class NotifyActionReceiver extends BroadcastReceiver {
    static final String ACTION = "net.airplot.blackbox.NOTIFY_ACTION";
    static final String ACT_SNOOZE = "snooze";
    static final String ACT_OFF = "off";
    static final String ACT_SNOOZE_ALL = "snoozeAll";
    static final String ACT_UNSNOOZE_ALL = "unsnoozeAll";
    static final String ACT_STOP = "stop";

    static PendingIntent pending(Context ctx, String act, String kind, int notifId, int requestCode) {
        Intent i = new Intent(ctx, NotifyActionReceiver.class)
            .setAction(ACTION)
            .putExtra("act", act)
            .putExtra("kind", kind)
            .putExtra("notifId", notifId);
        return PendingIntent.getBroadcast(ctx, requestCode, i, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    @Override
    public void onReceive(Context ctx, Intent intent) {
        if (!ACTION.equals(intent.getAction())) return;
        String act = intent.getStringExtra("act");
        String kind = intent.getStringExtra("kind");
        int notifId = intent.getIntExtra("notifId", 0);
        SharedPreferences p = NotifyPrefs.get(ctx);
        long now = System.currentTimeMillis();
        String msg = null;
        if (ACT_SNOOZE.equals(act) && kind != null && !kind.isEmpty()) {
            int min = p.getInt(NotifyPrefs.K_SHORTCUT_SNOOZE_MIN, 60);
            p.edit().putLong("snooze_" + kind, now + min * 60000L).apply();
            msg = NotifyCentreService.kindLabel(kind) + " snoozed until " + NotifyCentreService.hhmm(now + min * 60000L);
        } else if (ACT_OFF.equals(act) && kind != null && !kind.isEmpty()) {
            p.edit().putBoolean("on_" + kind, false).apply();
            msg = NotifyCentreService.kindLabel(kind) + " notifications off - turn back on in Blackbox Settings";
        } else if (ACT_SNOOZE_ALL.equals(act)) {
            p.edit().putLong(NotifyPrefs.K_SNOOZE_ALL, now + 3600000L).apply();
            msg = "All notifications snoozed until " + NotifyCentreService.hhmm(now + 3600000L);
        } else if (ACT_UNSNOOZE_ALL.equals(act)) {
            p.edit().putLong(NotifyPrefs.K_SNOOZE_ALL, 0L).apply();
            msg = "Notifications back on";
        } else if (ACT_STOP.equals(act)) {
            p.edit().putBoolean(NotifyPrefs.K_ENABLED, false).apply();
            NotifyCentreService.stop(ctx);
            msg = "Notification centre off - turn it on again in Blackbox Settings";
        }
        if (notifId != 0) NotificationManagerCompat.from(ctx).cancel(notifId);
        if (!ACT_STOP.equals(act) && p.getBoolean(NotifyPrefs.K_ENABLED, false)) NotifyCentreService.refreshServiceNotification(ctx);
        if (msg != null) {
            try { Toast.makeText(ctx, msg, Toast.LENGTH_LONG).show(); } catch (Exception e) { /* best-effort */ }
        }
    }
}
