package net.airplot.blackbox;

import android.Manifest;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Bundle;
import android.util.Log;
import android.webkit.WebView;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    private static final String TAG = "AirplotMain";

    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(TrackerNotificationPlugin.class);
        registerPlugin(DiagnosticsPlugin.class);
        registerPlugin(NotifyCentrePlugin.class);
        registerPlugin(ExternalLinkPlugin.class);
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
        keepWebViewAliveInBackground();
    }

    // All recording/upload logic runs as JavaScript inside the WebView. By default Android
    // drops the WebView's renderer to low priority as soon as the app isn't visible, and under
    // memory pressure kills it - the location notification stays up, but nothing records or
    // uploads any more. Keeping the renderer at IMPORTANT priority even while hidden
    // (waivedWhenNotVisible = false) ties its lifetime to the app process, which the
    // background-location foreground service keeps alive.
    private void keepWebViewAliveInBackground() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return;
        try {
            WebView webView = getBridge() != null ? getBridge().getWebView() : null;
            if (webView != null) {
                webView.setRendererPriorityPolicy(WebView.RENDERER_PRIORITY_IMPORTANT, false);
                Log.i(TAG, "WebView renderer priority set to IMPORTANT (kept while in background).");
            }
        } catch (Exception e) {
            Log.w(TAG, "Could not set WebView renderer priority: " + e.getMessage());
        }
    }

    // Safety net: if anything paused the WebView's JavaScript timers when the activity went to
    // the background, resume them so the page's upload/overdue timers keep running. Android
    // can still slow timers down while hidden - blackbox.html also uploads from GPS fixes.
    @Override
    public void onPause() {
        super.onPause();
        try {
            if (getBridge() != null && getBridge().getWebView() != null) {
                getBridge().getWebView().resumeTimers();
                getBridge().getWebView().onResume();
            }
        } catch (Exception e) {
            Log.w(TAG, "Could not keep WebView timers running: " + e.getMessage());
        }
    }
}
