package net.airplot.blackbox;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ServiceInfo;
import android.media.AudioAttributes;
import android.net.Uri;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;
import android.util.Log;
import androidx.core.app.NotificationCompat;
import androidx.core.app.NotificationManagerCompat;
import androidx.core.app.ServiceCompat;
import androidx.core.content.ContextCompat;
import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import org.json.JSONArray;
import org.json.JSONObject;

// Notification centre watcher (admins and event managers). A foreground service, so it keeps
// running with the screen off and the app closed: every few seconds it asks notify-feed.php for
// new events (trip started / ended, Mayday, Pan-Pan, cancel, overdue) and pops up a heads-up
// notification for each one the user has switched on and not snoozed, waking the screen.
// It runs on its own - blackbox.html only turns it on/off and changes its settings
// (NotifyCentrePlugin), so it keeps working when the app's page is asleep or closed.
public class NotifyCentreService extends Service {
    private static final String TAG = "AirplotNotify";

    static final String ACTION_CHECK_NOW = "net.airplot.blackbox.NOTIFY_CHECK_NOW";
    static final int SERVICE_NOTIF_ID = 4250;
    static final String CH_SERVICE = "airplot_watch_service";
    // One channel per kind, each with its own sound (res/raw/watch_*.wav). Android fixes a
    // channel's sound when it is first made, so these have new ids ("2"); the first-version
    // channels (alarm-clock ringtone) are deleted in ensureChannels().
    static final String CH_MAYDAY = "airplot_watch2_mayday";
    static final String CH_PANPAN = "airplot_watch2_panpan";
    static final String CH_CANCEL = "airplot_watch2_cancel";
    static final String CH_OVERDUE = "airplot_watch2_overdue";
    static final String CH_START = "airplot_watch2_start";
    static final String CH_END = "airplot_watch2_end";
    private static final String[] OLD_CHANNELS = {"airplot_watch_distress", "airplot_watch_overdue", "airplot_watch_trips"};

    private volatile boolean running = false;
    private Thread worker;
    private PowerManager.WakeLock cpuLock;

    // ---- start / stop from anywhere (plugin, boot receiver, notification buttons) ----
    static void start(Context ctx) {
        Intent i = new Intent(ctx, NotifyCentreService.class);
        ContextCompat.startForegroundService(ctx, i);
    }

    static void checkNow(Context ctx) {
        Intent i = new Intent(ctx, NotifyCentreService.class).setAction(ACTION_CHECK_NOW);
        ContextCompat.startForegroundService(ctx, i);
    }

    static void stop(Context ctx) {
        ctx.stopService(new Intent(ctx, NotifyCentreService.class));
    }

    @Override
    public IBinder onBind(Intent intent) { return null; }

    @Override
    public void onCreate() {
        super.onCreate();
        ensureChannels(this);
        // Keeps the CPU awake between checks while the screen is off - without it the check
        // loop stops as soon as the phone sleeps. Released when the watcher is turned off.
        PowerManager pm = (PowerManager) getSystemService(Context.POWER_SERVICE);
        if (pm != null) {
            cpuLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "airplot:notifycentre");
            cpuLock.setReferenceCounted(false);
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        SharedPreferences p = NotifyPrefs.get(this);
        goForeground(p);
        if (!p.getBoolean(NotifyPrefs.K_ENABLED, false) || p.getString(NotifyPrefs.K_TOKEN, "").isEmpty()) {
            stopSelf();
            return START_NOT_STICKY;
        }
        if (cpuLock != null && !cpuLock.isHeld()) cpuLock.acquire();
        if (worker == null || !worker.isAlive()) {
            running = true;
            worker = new Thread(this::loop, "airplot-notify");
            worker.start();
        } else if (intent != null && ACTION_CHECK_NOW.equals(intent.getAction())) {
            worker.interrupt(); // wakes the sleep - checks straight away with the new settings
        }
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        running = false;
        if (worker != null) worker.interrupt();
        if (cpuLock != null && cpuLock.isHeld()) cpuLock.release();
        super.onDestroy();
    }

    private void goForeground(SharedPreferences p) {
        Notification n = buildServiceNotification(this, p);
        try {
            if (Build.VERSION.SDK_INT >= 34) {
                ServiceCompat.startForeground(this, SERVICE_NOTIF_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
            } else {
                startForeground(SERVICE_NOTIF_ID, n);
            }
        } catch (Exception e) {
            Log.w(TAG, "startForeground failed: " + e.getMessage());
        }
    }

    private void loop() {
        while (running) {
            SharedPreferences p = NotifyPrefs.get(this);
            boolean more = false;
            long waitMs = NotifyPrefs.pollSec(p) * 1000L;
            try {
                more = pollOnce(p);
            } catch (AuthException e) {
                p.edit().putString(NotifyPrefs.K_LAST_ERR, e.getMessage()).apply();
                waitMs = 5 * 60 * 1000L; // signed out - try now and then, the user has to turn it on again
            } catch (Exception e) {
                p.edit().putString(NotifyPrefs.K_LAST_ERR, "No connection (" + e.getClass().getSimpleName() + ")").apply();
            }
            refreshServiceNotification(this);
            if (more) continue;
            try { Thread.sleep(waitMs); } catch (InterruptedException ie) { /* check now */ }
        }
    }

    static class AuthException extends Exception {
        AuthException(String m) { super(m); }
    }

    // One check. Returns true if the server has more events waiting (ask again straight away).
    private boolean pollOnce(SharedPreferences p) throws Exception {
        String base = p.getString(NotifyPrefs.K_BASE, "https://airplot.net");
        String token = p.getString(NotifyPrefs.K_TOKEN, "");
        long cursor = p.getLong(NotifyPrefs.K_CURSOR, -1L);
        p.edit().putLong(NotifyPrefs.K_LAST_TRY, System.currentTimeMillis()).apply();
        String url = base + "/notify-feed.php" + (cursor >= 0 ? ("?since=" + cursor) : "");
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setConnectTimeout(15000);
        c.setReadTimeout(15000);
        c.setUseCaches(false);
        c.setRequestProperty("X-Notify-Token", token);
        c.setRequestProperty("Accept", "application/json");
        int code = c.getResponseCode();
        String body = readAll(code >= 400 ? c.getErrorStream() : c.getInputStream());
        c.disconnect();
        JSONObject j = body.isEmpty() ? new JSONObject() : new JSONObject(body);
        if (code == 401 || code == 403) throw new AuthException(j.optString("message", "Signed out - turn the notification centre on again in Blackbox."));
        if (code != 200 || !j.optBoolean("ok", false)) throw new Exception("HTTP " + code);

        JSONArray events = j.optJSONArray("events");
        if (cursor >= 0 && events != null) {
            for (int i = 0; i < events.length(); i++) {
                JSONObject ev = events.optJSONObject(i);
                if (ev == null) continue;
                if (!NotifyPrefs.shouldShow(p, ev.optString("type"))) continue;
                postEvent(this, ev, false);
            }
        }
        p.edit()
            .putLong(NotifyPrefs.K_CURSOR, j.optLong("cursor", cursor < 0 ? 0 : cursor))
            .putLong(NotifyPrefs.K_LAST_OK, System.currentTimeMillis())
            .putString(NotifyPrefs.K_LAST_ERR, "")
            .putString(NotifyPrefs.K_ROLE, j.optString("role", p.getString(NotifyPrefs.K_ROLE, "")))
            .apply();
        return j.optBoolean("more", false);
    }

    private static String readAll(InputStream in) throws Exception {
        if (in == null) return "";
        StringBuilder sb = new StringBuilder();
        try (BufferedReader r = new BufferedReader(new InputStreamReader(in, StandardCharsets.UTF_8))) {
            String line;
            while ((line = r.readLine()) != null) sb.append(line);
        }
        return sb.toString();
    }

    // ---- channels ----
    static void ensureChannels(Context ctx) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return;
        NotificationManager nm = ctx.getSystemService(NotificationManager.class);
        if (nm == null) return;
        NotificationChannel svc = new NotificationChannel(CH_SERVICE, "Notification centre (running)", NotificationManager.IMPORTANCE_MIN);
        svc.setDescription("Shows that the notification centre is watching. Can be hidden.");
        svc.setShowBadge(false);
        nm.createNotificationChannel(svc);

        for (String old : OLD_CHANNELS) { try { nm.deleteNotificationChannel(old); } catch (Exception e) { /* already gone */ } }
        // Mayday / Pan-Pan play on the ALARM volume (heard even with notification sounds turned
        // down) and through Do Not Disturb; the rest on the normal notification volume.
        makeChannel(ctx, nm, CH_MAYDAY, "Watch: Mayday", "Mayday from a trip you watch - fast warning tone.",
            R.raw.watch_mayday, true, new long[]{0, 400, 150, 400, 150, 400, 150, 800});
        makeChannel(ctx, nm, CH_PANPAN, "Watch: Pan-Pan", "Pan-Pan from a trip you watch - double caution chime.",
            R.raw.watch_panpan, true, new long[]{0, 300, 200, 300});
        makeChannel(ctx, nm, CH_CANCEL, "Watch: Mayday / Pan-Pan cancelled", "The pilot cancelled their Mayday or Pan-Pan - calm rising chime.",
            R.raw.watch_cancel, false, new long[]{0, 150, 100, 150});
        makeChannel(ctx, nm, CH_OVERDUE, "Watch: Overdue", "A trip you watch passed its overdue time - three falling chimes.",
            R.raw.watch_overdue, false, new long[]{0, 300, 150, 300, 150, 600});
        makeChannel(ctx, nm, CH_START, "Watch: Recording started", "Someone you watch started recording - soft rising chime.",
            R.raw.watch_start, false, new long[]{0, 120});
        makeChannel(ctx, nm, CH_END, "Watch: Recording ended", "Someone you watch ended recording - soft falling chime.",
            R.raw.watch_end, false, new long[]{0, 120});
    }

    private static void makeChannel(Context ctx, NotificationManager nm, String id, String name, String desc,
                                    int soundRes, boolean urgent, long[] vibration) {
        NotificationChannel ch = new NotificationChannel(id, name, NotificationManager.IMPORTANCE_HIGH);
        ch.setDescription(desc);
        ch.enableVibration(true);
        ch.setVibrationPattern(vibration);
        AudioAttributes attrs = new AudioAttributes.Builder()
            .setUsage(urgent ? AudioAttributes.USAGE_ALARM : AudioAttributes.USAGE_NOTIFICATION_EVENT)
            .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build();
        ch.setSound(Uri.parse("android.resource://" + ctx.getPackageName() + "/" + soundRes), attrs);
        if (urgent) ch.setBypassDnd(true);
        ch.setLockscreenVisibility(Notification.VISIBILITY_PUBLIC);
        nm.createNotificationChannel(ch);
    }

    // ---- the always-on "watching" notification ----
    static Notification buildServiceNotification(Context ctx, SharedPreferences p) {
        ensureChannels(ctx);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE;
        Intent open = ctx.getPackageManager().getLaunchIntentForPackage(ctx.getPackageName());
        PendingIntent openPi = PendingIntent.getActivity(ctx, 40, open, flags);
        String role = p.getString(NotifyPrefs.K_ROLE, "");
        String title = "admin".equals(role) ? "Watching all trips" : "Watching your members' trips";
        String err = p.getString(NotifyPrefs.K_LAST_ERR, "");
        long ok = p.getLong(NotifyPrefs.K_LAST_OK, 0L);
        long snoozeAll = p.getLong(NotifyPrefs.K_SNOOZE_ALL, 0L);
        String text;
        if (!err.isEmpty()) text = err;
        else if (ok > 0) text = "Last check " + hhmm(ok);
        else text = "Starting...";
        if (snoozeAll > System.currentTimeMillis()) text = "All snoozed until " + hhmm(snoozeAll) + " - " + text;
        NotificationCompat.Builder b = new NotificationCompat.Builder(ctx, CH_SERVICE)
            .setSmallIcon(android.R.drawable.ic_popup_reminder)
            .setContentTitle("Airplot notification centre - " + title)
            .setContentText(text)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setPriority(NotificationCompat.PRIORITY_MIN)
            .setContentIntent(openPi)
            .addAction(0, snoozeAll > System.currentTimeMillis() ? "Unsnooze all" : "Snooze all 1 h",
                NotifyActionReceiver.pending(ctx, snoozeAll > System.currentTimeMillis() ? NotifyActionReceiver.ACT_UNSNOOZE_ALL : NotifyActionReceiver.ACT_SNOOZE_ALL, "", 0, 41))
            .addAction(0, "Turn off", NotifyActionReceiver.pending(ctx, NotifyActionReceiver.ACT_STOP, "", 0, 42));
        return b.build();
    }

    static void refreshServiceNotification(Context ctx) {
        try {
            NotificationManagerCompat.from(ctx).notify(SERVICE_NOTIF_ID, buildServiceNotification(ctx, NotifyPrefs.get(ctx)));
        } catch (SecurityException e) { /* notifications not allowed */ }
    }

    // ---- one event -> one heads-up notification, with its shortcuts ----
    static void postEvent(Context ctx, JSONObject ev, boolean isTest) {
        ensureChannels(ctx);
        String type = ev.optString("type");
        String kind = NotifyPrefs.kindOf(type);
        String reg = ev.optString("reg");
        if (reg.isEmpty()) reg = ev.optBoolean("private") ? "Private trip" : "Unknown";
        String what;
        String channel;
        switch (type) {
            case "mayday": what = "MAYDAY"; channel = CH_MAYDAY; break;
            case "mayday_cancel": what = "Mayday cancelled"; channel = CH_CANCEL; break;
            case "panpan": what = "PAN PAN"; channel = CH_PANPAN; break;
            case "panpan_cancel": what = "Pan-Pan cancelled"; channel = CH_CANCEL; break;
            case "overdue": what = "OVERDUE"; channel = CH_OVERDUE; break;
            case "start": what = "Started"; channel = CH_START; break;
            case "end": what = "Ended"; channel = CH_END; break;
            default: what = type; channel = CH_START; break;
        }
        boolean urgent = "mayday".equals(type) || "panpan".equals(type) || "overdue".equals(type);
        String title = (isTest ? "TEST - " : "") + what + " - " + reg;
        StringBuilder line = new StringBuilder();
        String pilot = ev.optString("pilot");
        if (!pilot.isEmpty()) line.append(pilot).append(" · ");
        line.append(ev.optString("mode", "Aviation"));
        String o = ev.optString("origin"), d = ev.optString("dest");
        if (!o.isEmpty() || !d.isEmpty()) line.append(" · ").append(o.isEmpty() ? "?" : o).append(" → ").append(d.isEmpty() ? "?" : d);
        long t = ev.optLong("t", System.currentTimeMillis());
        line.append(" · ").append(hhmm(t));
        StringBuilder big = new StringBuilder(line);
        String flight = ev.optString("flight");
        if (!flight.isEmpty()) big.append("\nFlight ").append(flight);
        String due = ev.optString("overdue");
        if (!due.isEmpty() && ("overdue".equals(type) || "start".equals(type))) big.append("\nDue ").append(due);
        String lat = ev.optString("lat"), lon = ev.optString("lon");
        if (!lat.isEmpty() && !lon.isEmpty()) big.append("\nLast position ").append(lat).append(", ").append(lon);

        int notifId = 6000 + (int) (Math.abs(ev.optLong("id", System.currentTimeMillis())) % 50000L);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE;
        String link = ev.optString("link");
        PendingIntent openMap;
        if (!link.isEmpty()) {
            Intent view = new Intent(Intent.ACTION_VIEW, Uri.parse(link)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            openMap = PendingIntent.getActivity(ctx, notifId, view, flags);
        } else {
            Intent open = ctx.getPackageManager().getLaunchIntentForPackage(ctx.getPackageName());
            openMap = PendingIntent.getActivity(ctx, notifId, open, flags);
        }
        SharedPreferences p = NotifyPrefs.get(ctx);
        int snoozeMin = p.getInt(NotifyPrefs.K_SHORTCUT_SNOOZE_MIN, 60);
        String kindLabel = kindLabel(kind);

        NotificationCompat.Builder b = new NotificationCompat.Builder(ctx, channel)
            .setSmallIcon(urgent ? android.R.drawable.stat_sys_warning : android.R.drawable.ic_popup_reminder)
            .setContentTitle(title)
            .setContentText(line.toString())
            .setStyle(new NotificationCompat.BigTextStyle().bigText(big.toString()))
            .setPriority(urgent ? NotificationCompat.PRIORITY_MAX : NotificationCompat.PRIORITY_HIGH)
            .setCategory(urgent ? NotificationCompat.CATEGORY_ALARM : NotificationCompat.CATEGORY_EVENT)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setWhen(t)
            .setShowWhen(true)
            .setAutoCancel(true)
            .setContentIntent(openMap)
            .addAction(0, "Open map", openMap)
            .addAction(0, "Snooze " + kindLabel + " " + durLabel(snoozeMin), NotifyActionReceiver.pending(ctx, NotifyActionReceiver.ACT_SNOOZE, kind, notifId, notifId * 4 + 1))
            .addAction(0, "Turn off " + kindLabel, NotifyActionReceiver.pending(ctx, NotifyActionReceiver.ACT_OFF, kind, notifId, notifId * 4 + 2));
        try {
            NotificationManagerCompat.from(ctx).notify(notifId, b.build());
        } catch (SecurityException e) {
            Log.w(TAG, "Notifications not allowed: " + e.getMessage());
        }
        wakeScreen(ctx);
    }

    // Lights the screen for a few seconds so the pop-up is seen with the phone locked.
    @SuppressWarnings("deprecation")
    private static void wakeScreen(Context ctx) {
        try {
            PowerManager pm = (PowerManager) ctx.getSystemService(Context.POWER_SERVICE);
            if (pm == null || pm.isInteractive()) return;
            PowerManager.WakeLock wl = pm.newWakeLock(
                PowerManager.SCREEN_BRIGHT_WAKE_LOCK | PowerManager.ACQUIRE_CAUSES_WAKEUP | PowerManager.ON_AFTER_RELEASE,
                "airplot:notifywake");
            wl.acquire(5000);
        } catch (Exception e) { /* best-effort */ }
    }

    static String kindLabel(String kind) {
        switch (kind) {
            case "mayday": return "Mayday";
            case "panpan": return "Pan-Pan";
            case "overdue": return "overdue";
            case "start": return "starts";
            case "end": return "ends";
            default: return kind;
        }
    }

    static String durLabel(int min) {
        if (min < 60) return min + " min";
        if (min % 60 == 0) return (min / 60) + " h";
        return (min / 60) + " h " + (min % 60) + " min";
    }

    static String hhmm(long ms) {
        return new SimpleDateFormat("HH:mm", Locale.getDefault()).format(new Date(ms));
    }
}
