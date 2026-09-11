package net.airplot.blackbox;

import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.graphics.Color;
import android.os.Build;
import android.widget.RemoteViews;
import androidx.core.app.NotificationCompat;
import androidx.core.app.NotificationManagerCompat;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

// Persistent "quick controls" notification - shows current mode/speed/altitude/heading and time
// since the last transmission, with a big Record/Stop square button, so recording can be checked
// or toggled from the notification shade without opening the app. Text color flips from green
// (idle) to red (recording) as an at-a-glance state indicator. This plugin holds no
// track/recording state of its own - it's driven entirely by blackbox.html calling
// show()/update()/hide() and listening for the "toggleRecord" event when the button is tapped.
@CapacitorPlugin(name = "TrackerNotification")
public class TrackerNotificationPlugin extends Plugin {

    private static final String CHANNEL_ID = "airplot_quick_controls";
    private static final int NOTIF_ID = 4242;
    private static final String ACTION_TOGGLE = "net.airplot.blackbox.ACTION_TOGGLE_RECORD";

    private static final int COLOR_IDLE_TITLE = Color.parseColor("#2E7D32");
    private static final int COLOR_IDLE_TEXT = Color.parseColor("#388E3C");
    private static final int COLOR_REC_TITLE = Color.parseColor("#C62828");
    private static final int COLOR_REC_TEXT = Color.parseColor("#D32F2F");

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
        render(speed, altitude, lastTransmit, recording != null && recording, mode, heading, overdue);
        call.resolve();
    }

    @PluginMethod
    public void hide(PluginCall call) {
        NotificationManagerCompat.from(getContext()).cancel(NOTIF_ID);
        call.resolve();
    }

    private void render(String speed, String altitude, String lastTransmit, boolean recording, String mode, String heading, String overdue) {
        Context context = getContext();
        int flags = PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE;

        // Explicit package on the toggle Intent - without this, some devices (particularly
        // OEM-customized Android builds with aggressive background restrictions) can silently
        // drop the implicit broadcast, so tapping the button in the notification does nothing.
        Intent toggleIntent = new Intent(ACTION_TOGGLE).setPackage(context.getPackageName());
        PendingIntent togglePending = PendingIntent.getBroadcast(context, 0, toggleIntent, flags);

        Intent openAppIntent = context.getPackageManager().getLaunchIntentForPackage(context.getPackageName());
        PendingIntent contentPending = PendingIntent.getActivity(context, 1, openAppIntent, flags);

        String title = recording
            ? ("Recording \u00b7 " + speed + " \u00b7 " + altitude + " \u00b7 " + heading)
            : "Airplot idle";
        String text = "Last transmit: " + lastTransmit
            + ((overdue != null && !overdue.isEmpty()) ? ("  \u00b7  " + overdue) : "");
        String btnLabel = recording ? "STOP" : "REC";
        int btnBg = recording ? R.drawable.notif_btn_stop : R.drawable.notif_btn_record;
        int titleColor = recording ? COLOR_REC_TITLE : COLOR_IDLE_TITLE;
        int textColor = recording ? COLOR_REC_TEXT : COLOR_IDLE_TEXT;

        RemoteViews compact = new RemoteViews(context.getPackageName(), R.layout.notification_quick_controls);
        compact.setTextViewText(R.id.notif_title, title);
        compact.setTextViewText(R.id.notif_text, text);
        compact.setInt(R.id.notif_title, "setTextColor", titleColor);
        compact.setInt(R.id.notif_text, "setTextColor", textColor);
        compact.setTextViewText(R.id.notif_action_btn, btnLabel);
        compact.setInt(R.id.notif_action_btn, "setBackgroundResource", btnBg);
        compact.setOnClickPendingIntent(R.id.notif_action_btn, togglePending);

        RemoteViews big = new RemoteViews(context.getPackageName(), R.layout.notification_quick_controls_big);
        big.setTextViewText(R.id.notif_title_big, title);
        big.setTextViewText(R.id.notif_text_big, text);
        big.setTextViewText(R.id.notif_mode_big, "Mode: " + mode);
        big.setInt(R.id.notif_title_big, "setTextColor", titleColor);
        big.setInt(R.id.notif_text_big, "setTextColor", textColor);
        big.setInt(R.id.notif_mode_big, "setTextColor", textColor);
        big.setTextViewText(R.id.notif_action_btn_big, btnLabel);
        big.setInt(R.id.notif_action_btn_big, "setBackgroundResource", btnBg);
        big.setOnClickPendingIntent(R.id.notif_action_btn_big, togglePending);

        NotificationCompat.Builder builder = new NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_menu_compass) // TODO: swap for the app's own small icon once one exists
            .setContentTitle(title)
            .setContentText(text)
            .setStyle(new NotificationCompat.DecoratedCustomViewStyle())
            .setCustomContentView(compact)
            .setCustomBigContentView(big)
            .setOngoing(recording)
            .setOnlyAlertOnce(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setContentIntent(contentPending);

        NotificationManagerCompat.from(context).notify(NOTIF_ID, builder.build());
    }
}
