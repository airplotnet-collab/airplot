package net.airplot.blackbox;

import android.content.Context;
import android.content.SharedPreferences;

// Settings of the notification centre, shared by the watcher service, the plugin (blackbox.html)
// and the notification buttons. Kept in SharedPreferences so they survive the app being closed
// and the phone restarting.
final class NotifyPrefs {
    private NotifyPrefs() {}

    static final String FILE = "airplot_notify";
    // Event kinds the pilot can turn on/off and snooze. A cancel follows its own kind
    // (mayday_cancel uses "mayday", panpan_cancel uses "panpan").
    static final String[] KINDS = {"mayday", "panpan", "overdue", "start", "end"};

    static final String K_ENABLED = "enabled";        // watcher on/off
    static final String K_TOKEN = "token";            // notify-feed.php token for this phone
    static final String K_BASE = "baseUrl";           // https://airplot.net
    static final String K_ROLE = "role";              // admin | event
    static final String K_EMAIL = "email";
    static final String K_CURSOR = "cursor";          // last event id seen (-1 = first check not done)
    static final String K_POLL_SEC = "pollSec";
    static final String K_SNOOZE_ALL = "snoozeAllUntil";
    static final String K_LAST_OK = "lastOkMs";
    static final String K_LAST_TRY = "lastTryMs";
    static final String K_LAST_ERR = "lastError";
    static final String K_SHORTCUT_SNOOZE_MIN = "shortcutSnoozeMin"; // the notification's Snooze button

    static SharedPreferences get(Context ctx) {
        return ctx.getApplicationContext().getSharedPreferences(FILE, Context.MODE_PRIVATE);
    }

    static String kindOf(String type) {
        if (type == null) return "";
        if (type.endsWith("_cancel")) return type.substring(0, type.length() - 7);
        return type;
    }

    static boolean kindOn(SharedPreferences p, String kind) {
        return p.getBoolean("on_" + kind, true);
    }

    static long kindSnoozedUntil(SharedPreferences p, String kind) {
        return p.getLong("snooze_" + kind, 0L);
    }

    // Should an event of this type pop up right now?
    static boolean shouldShow(SharedPreferences p, String type) {
        String kind = kindOf(type);
        long now = System.currentTimeMillis();
        if (!kindOn(p, kind)) return false;
        if (kindSnoozedUntil(p, kind) > now) return false;
        return p.getLong(K_SNOOZE_ALL, 0L) <= now;
    }

    static int pollSec(SharedPreferences p) {
        int s = p.getInt(K_POLL_SEC, 30);
        return Math.max(10, Math.min(600, s));
    }
}
