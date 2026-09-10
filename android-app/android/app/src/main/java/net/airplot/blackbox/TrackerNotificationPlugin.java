package net.airplot.blackbox;

import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.os.Build;
import androidx.core.app.NotificationCompat;
import androidx.core.app.NotificationManagerCompat;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

// Persistent "quick controls" notification - shows current speed/altitude and time since the
// last transmission, with a Start/Stop action button, so recording can be checked or toggled
// from the notification shade without opening the app. This plugin holds no track/recording
// state of its own - it's driven entirely by blackbox.html calling show()/update()/hide() and
// listening for the "toggleRecord" event when the action button is tapped.
@CapacitorPlugin(name = "TrackerNotification")
public class TrackerNotificationPlugin extends Plugin {

    private static final String CHANNEL_ID = "airplot_quick_controls";
    private static final int NOTIF_ID = 4242;
    private static final String ACTION_TOGGLE = "net.airplot.blackbox.ACTION_TOGGLE_RECORD";

    private BroadcastReceiver receiver;

    @Override
    public void load() {
        createChannel();
        receiver = new BroadcastReceiver() {
            @Override
            public void onReceive(Context ctx, Intent intent) {
                if (ACTION_TOGGLE.equals(intent.getAction())) {
                    notifyListeners("toggleRecord", new JSObject());
                }
            }
        };
        IntentFilter filter = new IntentFilter(ACTION_TOGGLE);
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

    @PluginMethod
    public void show(PluginCall call) {
        render("-", "-", "\u2014", false);
        call.resolve();
    }

    @PluginMethod
    public void update(PluginCall call) {
        String speed = call.getString("speed", "-");
        String altitude = call.getString("altitude", "-");
        String lastTransmit = call.getString("lastTransmit", "\u2014");
        Boolean recording = call.getBoolean("recording", false);
        render(speed, altitude, lastTransmit, recording != null && recording);
        call.resolve();
    }

    @PluginMethod
    public void hide(PluginCall call) {
        NotificationManagerCompat.from(getContext()).cancel(NOTIF_ID);
        call.resolve();
    }

    private void render(String speed, String altitude, String lastTransmit, boolean recording) {
        Context context = getContext();
        int flags = PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE;
        PendingIntent togglePending = PendingIntent.getBroadcast(context, 0, new Intent(ACTION_TOGGLE), flags);
        Intent openAppIntent = context.getPackageManager().getLaunchIntentForPackage(context.getPackageName());
        PendingIntent contentPending = PendingIntent.getActivity(context, 1, openAppIntent, flags);

        String actionLabel = recording ? "Stop" : "Start";
        int actionIcon = recording ? android.R.drawable.ic_media_pause : android.R.drawable.ic_media_play;

        NotificationCompat.Builder builder = new NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_menu_compass) // TODO: swap for the app's own small icon once one exists
            .setContentTitle(recording ? ("Recording \u00b7 " + speed + " \u00b7 " + altitude) : "Airplot idle")
            .setContentText("Last transmit: " + lastTransmit)
            .setOngoing(recording)
            .setOnlyAlertOnce(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setContentIntent(contentPending)
            .addAction(actionIcon, actionLabel, togglePending);

        NotificationManagerCompat.from(context).notify(NOTIF_ID, builder.build());
    }
}
