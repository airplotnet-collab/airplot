package net.airplot.blackbox;

import android.os.Bundle;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(TrackerNotificationPlugin.class);
        super.onCreate(savedInstanceState);
    }
}
