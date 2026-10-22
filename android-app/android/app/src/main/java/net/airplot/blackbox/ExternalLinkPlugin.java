package net.airplot.blackbox;

import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.net.Uri;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

// Opens a link in Chrome as its own app, outside Airplot. Used by blackbox.html for the Track
// Link "Open" button and My trips / Membership: inside the app those pages replaced the tracking
// screen with no way back. In Chrome they open separately, and the Back button or the app
// switcher returns to the recording.
// Falls back to the phone's default browser when Chrome isn't installed.
@CapacitorPlugin(name = "ExternalLink")
public class ExternalLinkPlugin extends Plugin {

    @PluginMethod
    public void open(PluginCall call) {
        String url = call.getString("url");
        if (url == null || !(url.startsWith("https://") || url.startsWith("http://"))) {
            call.reject("Only http(s) links can be opened.");
            return;
        }
        Intent intent = new Intent(Intent.ACTION_VIEW, Uri.parse(url));
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        intent.addCategory(Intent.CATEGORY_BROWSABLE);
        String app = "chrome";
        try {
            intent.setPackage("com.android.chrome");
            getContext().startActivity(intent);
        } catch (ActivityNotFoundException noChrome) {
            try {
                intent.setPackage(null);
                getContext().startActivity(intent);
                app = "default";
            } catch (ActivityNotFoundException noBrowser) {
                call.reject("No browser found on this phone.");
                return;
            }
        }
        JSObject result = new JSObject();
        result.put("browser", app);
        call.resolve(result);
    }
}
