package net.airplot.blackbox;

import android.Manifest;
import android.content.Context;
import android.content.pm.PackageManager;
import android.location.LocationManager;
import android.os.Build;
import android.os.PowerManager;
import androidx.core.content.ContextCompat;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

// One-shot system check for the Settings screen's "Diagnose GPS & Battery" button - reads
// device-level state JS has no way to see for itself (GPS/network location providers, the
// actual granted permission state, and whether battery optimization is exempted), and hands
// it all back in one call instead of several separate plugin round-trips.
@CapacitorPlugin(name = "Diagnostics")
public class DiagnosticsPlugin extends Plugin {

    @PluginMethod
    public void check(PluginCall call) {
        LocationManager lm = (LocationManager) getContext().getSystemService(Context.LOCATION_SERVICE);
        boolean gpsOn = false;
        boolean networkOn = false;
        try { gpsOn = lm != null && lm.isProviderEnabled(LocationManager.GPS_PROVIDER); } catch (Exception e) {}
        try { networkOn = lm != null && lm.isProviderEnabled(LocationManager.NETWORK_PROVIDER); } catch (Exception e) {}

        boolean fineGranted = ContextCompat.checkSelfPermission(getContext(), Manifest.permission.ACCESS_FINE_LOCATION)
            == PackageManager.PERMISSION_GRANTED;
        boolean backgroundGranted = true; // permission didn't exist before Android 10
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            backgroundGranted = ContextCompat.checkSelfPermission(getContext(), Manifest.permission.ACCESS_BACKGROUND_LOCATION)
                == PackageManager.PERMISSION_GRANTED;
        }
        boolean notificationsGranted = true; // permission didn't exist before Android 13
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            notificationsGranted = ContextCompat.checkSelfPermission(getContext(), Manifest.permission.POST_NOTIFICATIONS)
                == PackageManager.PERMISSION_GRANTED;
        }

        boolean batteryExempt = false;
        PowerManager pm = (PowerManager) getContext().getSystemService(Context.POWER_SERVICE);
        try { batteryExempt = pm != null && pm.isIgnoringBatteryOptimizations(getContext().getPackageName()); } catch (Exception e) {}

        JSObject result = new JSObject();
        result.put("gpsProviderEnabled", gpsOn);
        result.put("networkProviderEnabled", networkOn);
        result.put("fineLocationGranted", fineGranted);
        result.put("backgroundLocationGranted", backgroundGranted);
        result.put("notificationsGranted", notificationsGranted);
        result.put("batteryOptimizationExempt", batteryExempt);
        call.resolve(result);
    }
}
