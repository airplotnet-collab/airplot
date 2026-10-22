package net.airplot.blackbox;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

// Fires at the overdue reminder time and at the overdue time itself (alarms set by
// TrackerNotificationPlugin.scheduleOverdueAlarms() from blackbox.html's overdue time). Posts
// the same heads-up alert notification as alert(), with Snooze, without needing the WebView to
// be awake - Android pauses the page's timers while the screen is off or the app is in the
// background. Emailing the emergency contact stays with blackbox.html and the server watchdog.
public class OverdueAlarmReceiver extends BroadcastReceiver {
    static final String ACTION_DUE = "net.airplot.blackbox.ACTION_OVERDUE_DUE";
    static final String ACTION_REMINDER = "net.airplot.blackbox.ACTION_OVERDUE_REMINDER";

    @Override
    public void onReceive(Context ctx, Intent intent) {
        boolean due = ACTION_DUE.equals(intent.getAction());
        String title = due ? "OVERDUE" : "Overdue reminder";
        String text = due
            ? "Your overdue time has passed - your emergency contact is being notified. Open Airplot or Snooze."
            : "Your overdue time is coming up - open Airplot to extend it, or Snooze.";
        try {
            TrackerNotificationPlugin.postAlertNotification(ctx, title, text, true);
        } catch (Exception e) { /* best-effort */ }
    }
}
