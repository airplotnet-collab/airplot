import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'net.airplot.blackbox',
  appName: 'Airplot Flight Recorder',
  webDir: 'www',
  // Points at the LIVE site instead of bundling local HTML — this is the whole reason a
  // rebuild isn't needed for ordinary blackbox.html/main.html/dashboard.html/launch.html
  // edits. Only changes to this file itself (permissions, plugin config) need a rebuild.
  server: {
    url: 'https://airplot.net/blackbox.html',
    // Every other Airplot page the app might navigate to (in-app links, the launch screen's
    // Dashboard/Flight Data Recorder links, dashboard's "3D" button, etc.) — without these,
    // Capacitor blocks navigation to anything not explicitly allowed and silently opens the
    // system browser instead, which would kick the person straight out of the app.
    allowNavigation: [
      'airplot.net',
      '*.airplot.net'
    ]
  },
  android: {
    allowMixedContent: false
  }
};

export default config;
