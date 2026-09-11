package net.airplot.blackbox;

import android.Manifest;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Bundle;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(TrackerNotificationPlugin.class);
        registerPlugin(DiagnosticsPlugin.class);
        super.onCreate(savedInstanceState);
        // Android 13+ (API 33) requires POST_NOTIFICATIONS to be granted at runtime -
        // the manifest <uses-permission> alone does nothing. Without this request,
        // TrackerNotificationPlugin's notify() calls silently no-op: no crash, no
        // error, no notification. Ask for it once on launch.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                    != PackageManager.PERMISSION_GRANTED) {
                ActivityCompat.requestPermissions(
                    this, new String[]{Manifest.permission.POST_NOTIFICATIONS}, 1001);
            }
        }
    }
}
