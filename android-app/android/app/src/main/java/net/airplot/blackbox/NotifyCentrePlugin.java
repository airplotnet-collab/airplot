package net.airplot.blackbox;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.PowerManager;
import androidx.core.app.NotificationManagerCompat;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import java.util.Iterator;
import org.json.JSONObject;

// blackbox.html's handle on the notification centre (Settings > Notification centre):
//   start({token, baseUrl, role, email})  turn the watcher on with this phone's feed token
//   stop()                                turn it off
//   setOptions({kinds:{mayday:{on,snoozeUntil},...}, snoozeAllUntil, pollSec, shortcutSnoozeMin})
//   getState()                            everything above + last check / error / permissions
//   test({type})                          pop up a sample notification
// The watcher itself (NotifyCentreService) runs on its own, so it keeps working while this
// page is asleep or the app is closed.
@CapacitorPlugin(name = "NotifyCentre")
public class NotifyCentrePlugin extends Plugin {

    @PluginMethod
    public void start(PluginCall call) {
        String token = call.getString("token", "");
        if (token == null || token.isEmpty()) { call.reject("No token"); return; }
        SharedPreferences p = NotifyPrefs.get(getContext());
        SharedPreferences.Editor e = p.edit();
        if (!token.equals(p.getString(NotifyPrefs.K_TOKEN, ""))) e.putLong(NotifyPrefs.K_CURSOR, -1L); // new sign-in: start from "now"
        e.putString(NotifyPrefs.K_TOKEN, token)
            .putString(NotifyPrefs.K_BASE, call.getString("baseUrl", "https://airplot.net"))
            .putString(NotifyPrefs.K_ROLE, call.getString("role", ""))
            .putString(NotifyPrefs.K_EMAIL, call.getString("email", ""))
            .putString(NotifyPrefs.K_LAST_ERR, "")
            .putBoolean(NotifyPrefs.K_ENABLED, true)
            .apply();
        try {
            NotifyCentreService.start(getContext());
        } catch (Exception ex) {
            call.reject("Could not start the watcher: " + ex.getMessage());
            return;
        }
        call.resolve(state());
    }

    @PluginMethod
    public void stop(PluginCall call) {
        NotifyPrefs.get(getContext()).edit()
            .putBoolean(NotifyPrefs.K_ENABLED, false)
            .putString(NotifyPrefs.K_TOKEN, "")
            .putLong(NotifyPrefs.K_CURSOR, -1L)
            .apply();
        NotifyCentreService.stop(getContext());
        call.resolve(state());
    }

    @PluginMethod
    public void setOptions(PluginCall call) {
        SharedPreferences p = NotifyPrefs.get(getContext());
        SharedPreferences.Editor e = p.edit();
        JSObject kinds = call.getObject("kinds", null);
        if (kinds != null) {
            Iterator<String> it = kinds.keys();
            while (it.hasNext()) {
                String k = it.next();
                JSONObject o = kinds.optJSONObject(k);
                if (o == null) continue;
                if (o.has("on")) e.putBoolean("on_" + k, o.optBoolean("on", true));
                if (o.has("snoozeUntil")) e.putLong("snooze_" + k, o.optLong("snoozeUntil", 0L));
            }
        }
        if (call.getData().has("snoozeAllUntil")) e.putLong(NotifyPrefs.K_SNOOZE_ALL, call.getData().optLong("snoozeAllUntil", 0L));
        if (call.getData().has("pollSec")) e.putInt(NotifyPrefs.K_POLL_SEC, call.getData().optInt("pollSec", 30));
        if (call.getData().has("shortcutSnoozeMin")) e.putInt(NotifyPrefs.K_SHORTCUT_SNOOZE_MIN, call.getData().optInt("shortcutSnoozeMin", 60));
        e.apply();
        if (p.getBoolean(NotifyPrefs.K_ENABLED, false)) {
            try { NotifyCentreService.checkNow(getContext()); } catch (Exception ex) { /* refreshed on the next check */ }
        }
        call.resolve(state());
    }

    @PluginMethod
    public void getState(PluginCall call) {
        call.resolve(state());
    }

    @PluginMethod
    public void test(PluginCall call) {
        String type = call.getString("type", "mayday");
        try {
            JSONObject ev = new JSONObject();
            ev.put("id", System.currentTimeMillis() % 100000);
            ev.put("t", System.currentTimeMillis());
            ev.put("type", type);
            ev.put("reg", "C-TEST");
            ev.put("pilot", "Test pilot");
            ev.put("mode", "Aviation");
            ev.put("origin", "CYEG");
            ev.put("dest", "CYYC");
            ev.put("link", "https://airplot.net/main.html");
            NotifyCentreService.postEvent(getContext(), ev, true);
            call.resolve();
        } catch (Exception ex) {
            call.reject(ex.getMessage());
        }
    }

    private JSObject state() {
        Context ctx = getContext();
        SharedPreferences p = NotifyPrefs.get(ctx);
        JSObject r = new JSObject();
        r.put("enabled", p.getBoolean(NotifyPrefs.K_ENABLED, false));
        r.put("hasToken", !p.getString(NotifyPrefs.K_TOKEN, "").isEmpty());
        r.put("role", p.getString(NotifyPrefs.K_ROLE, ""));
        r.put("email", p.getString(NotifyPrefs.K_EMAIL, ""));
        r.put("lastOkMs", p.getLong(NotifyPrefs.K_LAST_OK, 0L));
        r.put("lastTryMs", p.getLong(NotifyPrefs.K_LAST_TRY, 0L));
        r.put("lastError", p.getString(NotifyPrefs.K_LAST_ERR, ""));
        r.put("pollSec", NotifyPrefs.pollSec(p));
        r.put("shortcutSnoozeMin", p.getInt(NotifyPrefs.K_SHORTCUT_SNOOZE_MIN, 60));
        r.put("snoozeAllUntil", p.getLong(NotifyPrefs.K_SNOOZE_ALL, 0L));
        JSObject kinds = new JSObject();
        for (String k : NotifyPrefs.KINDS) {
            JSObject o = new JSObject();
            o.put("on", NotifyPrefs.kindOn(p, k));
            o.put("snoozeUntil", NotifyPrefs.kindSnoozedUntil(p, k));
            kinds.put(k, o);
        }
        r.put("kinds", kinds);
        r.put("notificationsAllowed", NotificationManagerCompat.from(ctx).areNotificationsEnabled());
        boolean exempt = false;
        try {
            PowerManager pm = (PowerManager) ctx.getSystemService(Context.POWER_SERVICE);
            exempt = pm != null && pm.isIgnoringBatteryOptimizations(ctx.getPackageName());
        } catch (Exception e) { /* unknown */ }
        r.put("batteryExempt", exempt);
        return r;
    }
}
