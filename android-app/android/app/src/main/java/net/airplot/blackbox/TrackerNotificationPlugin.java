package net.airplot.blackbox;

import android.app.AlarmManager;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.graphics.Color;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.os.VibrationEffect;
import android.os.Vibrator;
import android.os.VibratorManager;
import android.view.View;
import android.widget.RemoteViews;
import androidx.core.app.NotificationCompat;
import androidx.core.app.NotificationManagerCompat;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

// Persistent "quick controls" notification - collapsed view shows a one-line summary; expanded
// (pulled down further) shows five lines: Recording/Airplot idle, speed/altitude/heading,
// last transmit, overdue countdown (hidden unless applicable), and mode - plus a big Record/Stop
// square button. Text color flips from green (idle) to red (recording) as an at-a-glance state
// indicator. This plugin holds no track/recording state of its own - it's driven entirely by
// blackbox.html calling show()/update()/hide() and listening for "toggleRecord" on tap.
//
// Also exposes alert() - a separate, best-effort native vibrate + heads-up sound notification
// for Mayday/overdue alerts. blackbox.html's own Web Audio + navigator.vibrate() chime only
// works while the WebView is in the foreground (Android suspends both APIs once the app is
// backgrounded, same restriction any web page would have). alert() runs entirely in native
// code, so it keeps working while the app is minimized - the same way the background location
// service keeps running independent of the WebView being visible.
@CapacitorPlugin(name = "TrackerNotification")
public class TrackerNotificationPlugin extends Plugin {

    private static final String CHANNEL_ID = "airplot_quick_controls";
    private static final int NOTIF_ID = 4242;
    private static final String ACTION_TOGGLE = "net.airplot.blackbox.ACTION_TOGGLE_RECORD";
    static final String ACTION_SNOOZE = "net.airplot.blackbox.ACTION_SNOOZE_OVERDUE";
    private static final String ACTION_CLOSE = "net.airplot.blackbox.ACTION_CLOSE_APP";
    // Service started by @capacitor-community/background-geolocation - stopped on Close so
    // the location notification goes away with the app.
    private static final String GEO_SERVICE_CLASS = "com.equimaps.capacitor_background_geolocation.BackgroundGeolocationService";

    static final String ALERT_CHANNEL_ID = "airplot_alerts";
    static final int ALERT_NOTIF_ID = 4243;

    // Overdue alarms (OverdueAlarmReceiver): request codes for the two PendingIntents, and the
    // times last scheduled, so update() (called every few seconds) only reschedules on a change.
    private static final int REQ_OVERDUE_DUE = 10;
    private static final int REQ_OVERDUE_REMINDER = 11;
    private long scheduledOverdueAtMs = -1;
    private long scheduledReminderMs = -1;

    private static final int COLOR_IDLE_TITLE = Color.parseColor("#2E7D32");
    private static final int COLOR_IDLE_TEXT = Color.parseColor("#388E3C");
    private static final int COLOR_REC_TITLE = Color.parseColor("#C62828");
    private static final int COLOR_REC_TEXT = Color.parseColor("#D32F2F");

    private BroadcastReceiver receiver;
    // Last recording state sent by blackbox.html - Close is hidden and ignored while recording.
    private volatile boolean lastRecording = false;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private Runnable pendingExit;

    @Override
    public void load() {
        createChannel();
        createAlertChannel();
        receiver = new BroadcastReceiver() {
            @Override
            public void onReceive(Context ctx, Intent intent) {
                if (ACTION_TOGGLE.equals(intent.getAction())) {
                    notifyListeners("toggleRecord", new JSObject());
                } else if (ACTION_SNOOZE.equals(intent.getAction())) {
                    // blackbox.html owns the actual snooze logic (pushing the overdue time
                    // forward, clearing overdueNotified, re-arming the countdown) - this just
                    // relays the tap. setAutoCancel(true) on the action's own PendingIntent
                    // dismisses the notification itself.
                    notifyListeners("snoozeOverdue", new JSObject());
                } else if (ACTION_CLOSE.equals(intent.getAction())) {
                    if (lastRecording) return; // button is hidden while recording; ignore a stale tap
                    // blackbox.html stops GPS and calls exitApp(). If the page doesn't answer
                    // (WebView suspended or gone), close anyway after 3 seconds.
                    notifyListeners("closeApp", new JSObject());
                    if (pendingExit != null) handler.removeCallbacks(pendingExit);
                    pendingExit = TrackerNotificationPlugin.this::exitNow;
                    handler.postDelayed(pendingExit, 3000);
                }
            }
        };
        IntentFilter filter = new IntentFilter(ACTION_TOGGLE);
        filter.addAction(ACTION_SNOOZE);
        filter.addAction(ACTION_CLOSE);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            getContext().registerReceiver(receiver, filter, Context.RECEIVER_NOT_EXPORTED);
        } else {
            getContext().registerReceiver(receiver, filter);
        }
    }

    private void createChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel channel = new NotificationChannel(
                CHANNEL_ID, "Flight Quick Controls", NotificationManager.IMPORTANCE_LOW
            );
            channel.setShowBadge(false);
            NotificationManager nm = getContext().getSystemService(NotificationManager.class);
            nm.createNotificationChannel(channel);
        }
    }

    // Separate HIGH-importance channel so alert() actually pops up with sound - the quick
    // controls channel above is deliberately LOW/silent and would never do that.
    private void createAlertChannel() {
        ensureAlertChannel(getContext());
    }

    // Static so OverdueAlarmReceiver can make sure the channel exists even if the plugin
    // hasn't loaded in this process yet.
    static void ensureAlertChannel(Context ctx) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel channel = new NotificationChannel(
                ALERT_CHANNEL_ID, "Flight Alerts", NotificationManager.IMPORTANCE_HIGH
            );
            channel.setDescription("Overdue / Mayday alerts - sound and vibration.");
            channel.enableVibration(true);
            channel.setVibrationPattern(new long[]{0, 300, 150, 300, 150, 300, 150, 600});
            NotificationManager nm = ctx.getSystemService(NotificationManager.class);
            nm.createNotificationChannel(channel);
        }
    }

    // The heads-up alert notification itself - used by alert() and by OverdueAlarmReceiver.
    static void postAlertNotification(Context ctx, String title, String text, boolean showSnooze) {
        ensureAlertChannel(ctx);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE;
        Intent openAppIntent = ctx.getPackageManager().getLaunchIntentForPackage(ctx.getPackageName());
        PendingIntent contentPending = PendingIntent.getActivity(ctx, 2, openAppIntent, flags);
        NotificationCompat.Builder builder = new NotificationCompat.Builder(ctx, ALERT_CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_menu_compass) // TODO: swap for the app's own small icon once one exists
            .setContentTitle(title)
            .setContentText(text)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setAutoCancel(true)
            .setContentIntent(contentPending);
        if (showSnooze) {
            Intent snoozeIntent = new Intent(ACTION_SNOOZE).setPackage(ctx.getPackageName());
            PendingIntent snoozePending = PendingIntent.getBroadcast(ctx, 3, snoozeIntent, flags);
            builder.addAction(0, "Snooze +15 min", snoozePending);
        }
        NotificationManagerCompat.from(ctx).notify(ALERT_NOTIF_ID, builder.build());
    }

    // Android alarms for the overdue reminder and the overdue time itself. They fire even
    // while the WebView is asleep (screen off, app in the background) - blackbox.html's own
    // 1-second timer is paused by Android then, so on its own the alert only appeared once
    // the app was opened again. atMs <= 0 (not recording / no overdue time) cancels both.
    private void scheduleOverdueAlarms(long atMs, long reminderMs) {
        if (atMs == scheduledOverdueAtMs && reminderMs == scheduledReminderMs) return;
        scheduledOverdueAtMs = atMs;
        scheduledReminderMs = reminderMs;
        Context ctx = getContext();
        AlarmManager am = (AlarmManager) ctx.getSystemService(Context.ALARM_SERVICE);
        if (am == null) return;
        PendingIntent due = overdueAlarmIntent(ctx, REQ_OVERDUE_DUE, true);
        PendingIntent reminder = overdueAlarmIntent(ctx, REQ_OVERDUE_REMINDER, false);
        am.cancel(due);
        am.cancel(reminder);
        if (atMs <= 0) return;
        long now = System.currentTimeMillis();
        if (atMs > now) setWakeAlarm(am, atMs, due);
        long reminderAt = atMs - reminderMs;
        if (reminderMs > 0 && reminderAt > now) setWakeAlarm(am, reminderAt, reminder);
    }

    private static PendingIntent overdueAlarmIntent(Context ctx, int requestCode, boolean due) {
        Intent i = new Intent(ctx, OverdueAlarmReceiver.class)
            .setAction(due ? OverdueAlarmReceiver.ACTION_DUE : OverdueAlarmReceiver.ACTION_REMINDER);
        return PendingIntent.getBroadcast(ctx, requestCode, i, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    // Exact (to the second) when Android allows exact alarms for the app; otherwise a normal
    // alarm, which Android may hold back by a few minutes while the phone is idle.
    private static void setWakeAlarm(AlarmManager am, long atMs, PendingIntent pi) {
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S && !am.canScheduleExactAlarms()) {
                am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, atMs, pi);
            } else {
                am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, atMs, pi);
            }
        } catch (SecurityException e) {
            am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, atMs, pi);
        }
    }

    @PluginMethod
    public void show(PluginCall call) {
        render("-", "-", "\u2014", false, "Aviation", "-", "");
        call.resolve();
    }

    @PluginMethod
    public void update(PluginCall call) {
        String speed = call.getString("speed", "-");
        String altitude = call.getString("altitude", "-");
        String lastTransmit = call.getString("lastTransmit", "\u2014");
        String mode = call.getString("mode", "Aviation");
        String heading = call.getString("heading", "-");
        String overdue = call.getString("overdue", "");
        Boolean recording = call.getBoolean("recording", false);
        boolean isRecording = recording != null && recording;
        render(speed, altitude, lastTransmit, isRecording, mode, heading, overdue);
        Double overdueAt = call.getDouble("overdueAtMs", 0.0);
        Double reminder = call.getDouble("reminderMs", 0.0);
        try {
            scheduleOverdueAlarms(isRecording && overdueAt != null ? overdueAt.longValue() : 0L,
                                  reminder != null ? reminder.longValue() : 0L);
        } catch (Exception e) { /* best-effort - the in-app alert still works while the app is open */ }
        call.resolve();
    }

    @PluginMethod
    public void hide(PluginCall call) {
        NotificationManagerCompat.from(getContext()).cancel(NOTIF_ID);
        call.resolve();
    }

    // Called by blackbox.html after the notification's Close button, once GPS is stopped.
    @PluginMethod
    public void exitApp(PluginCall call) {
        call.resolve();
        handler.post(this::exitNow);
    }

    // Removes the notifications, stops the background location service, closes the app
    // (also removing it from Recents) and ends the process.
    private void exitNow() {
        if (pendingExit != null) { handler.removeCallbacks(pendingExit); pendingExit = null; }
        Context ctx = getContext();
        try {
            NotificationManagerCompat.from(ctx).cancel(NOTIF_ID);
            NotificationManagerCompat.from(ctx).cancel(ALERT_NOTIF_ID);
        } catch (Exception e) { /* best-effort */ }
        try {
            ctx.stopService(new Intent().setClassName(ctx.getPackageName(), GEO_SERVICE_CLASS));
        } catch (Exception e) { /* service not running, or a different plugin version */ }
        try {
            android.app.Activity act = getActivity();
            if (act != null) act.finishAndRemoveTask();
        } catch (Exception e) { /* best-effort */ }
        handler.postDelayed(() -> android.os.Process.killProcess(android.os.Process.myPid()), 400);
    }

    // Best-effort native vibrate + heads-up alert notification. Runs entirely outside the
    // WebView, so unlike blackbox.html's own navigator.vibrate()/Web Audio chime, this keeps
    // working while the app is backgrounded/minimized. showSnooze adds a "Snooze +15 min"
    // action button directly on the notification (overdue alerts only - doesn't make sense
    // for a Mayday, which isn't on a timer).
    @PluginMethod
    public void alert(PluginCall call) {
        boolean urgent = Boolean.TRUE.equals(call.getBoolean("urgent", false));
        boolean showSnooze = Boolean.TRUE.equals(call.getBoolean("showSnooze", false));
        String title = call.getString("title", urgent ? "MAYDAY" : "Alert");
        String text = call.getString("text", "");

        try {
            // Longer, more insistent patterns than a single one-shot buzz - more pulses and
            // a longer final buzz, so it's harder to miss/mistake for a routine notification.
            long[] pattern = urgent
                ? new long[]{0, 300, 150, 300, 150, 300, 150, 600}
                : new long[]{0, 200, 100, 200, 100, 200};
            Vibrator vibrator = null;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                VibratorManager vm = (VibratorManager) getContext().getSystemService(Context.VIBRATOR_MANAGER_SERVICE);
                if (vm != null) vibrator = vm.getDefaultVibrator();
            } else {
                vibrator = (Vibrator) getContext().getSystemService(Context.VIBRATOR_SERVICE);
            }
            if (vibrator != null) {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    vibrator.vibrate(VibrationEffect.createWaveform(pattern, -1));
                } else {
                    vibrator.vibrate(pattern, -1);
                }
            }
        } catch (Exception e) {
            // Best-effort - a vibration failure shouldn't block the notification below.
        }

        try {
            postAlertNotification(getContext(), title, text, showSnooze);
        } catch (Exception e) {
            // Best-effort - vibration above still fires even if the notification post fails.
        }

        call.resolve();
    }

    private void render(String speed, String altitude, String lastTransmit, boolean recording, String mode, String heading, String overdue) {
        Context context = getContext();
        int flags = PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE;
        lastRecording = recording;

        // Close button - hidden while recording, so a stray tap can't end a trip mid-flight.
        Intent closeIntent = new Intent(ACTION_CLOSE).setPackage(context.getPackageName());
        PendingIntent closePending = PendingIntent.getBroadcast(context, 4, closeIntent, flags);
        int closeVisibility = recording ? View.GONE : View.VISIBLE;

        // Explicit package on the toggle Intent - without this, some devices (particularly
        // OEM-customized Android builds with aggressive background restrictions) can silently
        // drop the implicit broadcast, so tapping the button in the notification does nothing.
        Intent toggleIntent = new Intent(ACTION_TOGGLE).setPackage(context.getPackageName());
        PendingIntent togglePending = PendingIntent.getBroadcast(context, 0, toggleIntent, flags);

        Intent openAppIntent = context.getPackageManager().getLaunchIntentForPackage(context.getPackageName());
        PendingIntent contentPending = PendingIntent.getActivity(context, 1, openAppIntent, flags);

        String title = recording ? "Recording" : "Airplot idle";
        String speedLine = speed + " \u00b7 " + altitude + " \u00b7 " + heading;
        String transmitLine = "Last transmit: " + lastTransmit;
        boolean hasOverdue = overdue != null && !overdue.isEmpty();
        String overdueLine = hasOverdue ? ("Overdue in: " + overdue) : "";
        String modeLine = "Mode: " + mode;

        String btnLabel = recording ? "STOP" : "REC";
        int btnBg = recording ? R.drawable.notif_btn_stop : R.drawable.notif_btn_record;
        int titleColor = recording ? COLOR_REC_TITLE : COLOR_IDLE_TITLE;
        int textColor = recording ? COLOR_REC_TEXT : COLOR_IDLE_TEXT;

        // Collapsed view only has room for two lines: title, then either the speed/altitude/
        // heading summary (while recording) or last-transmit (while idle, matching the old
        // behaviour since speed/heading aren't meaningful before a track has started).
        RemoteViews compact = new RemoteViews(context.getPackageName(), R.layout.notification_quick_controls);
        compact.setTextViewText(R.id.notif_title, title);
        compact.setTextViewText(R.id.notif_text, recording ? speedLine : transmitLine);
        compact.setInt(R.id.notif_title, "setTextColor", titleColor);
        compact.setInt(R.id.notif_text, "setTextColor", textColor);
        compact.setTextViewText(R.id.notif_action_btn, btnLabel);
        compact.setInt(R.id.notif_action_btn, "setBackgroundResource", btnBg);
        compact.setOnClickPendingIntent(R.id.notif_action_btn, togglePending);
        compact.setOnClickPendingIntent(R.id.notif_close_btn, closePending);
        compact.setViewVisibility(R.id.notif_close_btn, closeVisibility);

        RemoteViews big = new RemoteViews(context.getPackageName(), R.layout.notification_quick_controls_big);
        big.setTextViewText(R.id.notif_title_big, title);
        big.setInt(R.id.notif_title_big, "setTextColor", titleColor);

        big.setTextViewText(R.id.notif_speed_big, speedLine);
        big.setInt(R.id.notif_speed_big, "setTextColor", textColor);
        big.setViewVisibility(R.id.notif_speed_big, recording ? View.VISIBLE : View.GONE);

        big.setTextViewText(R.id.notif_text_big, transmitLine);
        big.setInt(R.id.notif_text_big, "setTextColor", textColor);

        big.setTextViewText(R.id.notif_overdue_big, overdueLine);
        big.setInt(R.id.notif_overdue_big, "setTextColor", textColor);
        big.setViewVisibility(R.id.notif_overdue_big, hasOverdue ? View.VISIBLE : View.GONE);

        big.setTextViewText(R.id.notif_mode_big, modeLine);
        big.setInt(R.id.notif_mode_big, "setTextColor", textColor);

        big.setTextViewText(R.id.notif_action_btn_big, btnLabel);
        big.setInt(R.id.notif_action_btn_big, "setBackgroundResource", btnBg);
        big.setOnClickPendingIntent(R.id.notif_action_btn_big, togglePending);
        big.setOnClickPendingIntent(R.id.notif_close_btn_big, closePending);
        big.setViewVisibility(R.id.notif_close_btn_big, closeVisibility);

        NotificationCompat.Builder builder = new NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_menu_compass) // TODO: swap for the app's own small icon once one exists
            .setContentTitle(title)
            .setContentText(recording ? speedLine : transmitLine)
            .setStyle(new NotificationCompat.DecoratedCustomViewStyle())
            .setCustomContentView(compact)
            .setCustomBigContentView(big)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setContentIntent(contentPending);

        NotificationManagerCompat.from(context).notify(NOTIF_ID, builder.build());
    }
}
