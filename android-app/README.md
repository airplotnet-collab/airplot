# Airplot Android app (Capacitor wrapper)

A thin native Android shell around the live site at https://airplot.net/blackbox.html — this
exists for exactly one reason: browsers cannot keep sending GPS updates once the screen locks
or the app is backgrounded (see capacitor.config.ts's comment for the mechanism). This wrapper
adds a real Android foreground service (via `@capacitor-community/background-geolocation`) so
tracking keeps running with the screen off.

## What lives here vs. what stays on GoDaddy

- **blackbox.html, main.html, dashboard.html, launch.html and everything else** stay exactly
  where they already are, edited exactly the way they already are. This repo does not contain
  copies of them.
- **This folder** contains only the native Android project (`android/`), Capacitor config, and
  the background-location plugin. `capacitor.config.ts`'s `server.url` points at the live site,
  so the app always loads whatever is currently on airplot.net — no rebuild needed for ordinary
  site edits.
- A rebuild is only needed when something in *this* folder changes — permissions, the plugin
  version, app icon, etc. That should be rare.

## Building

Pushes to `main` that touch `android-app/**` trigger `.github/workflows/build-android.yml`
automatically. It always produces an unsigned debug APK (installable directly on a device for
testing) as a downloadable build artifact. A signed release build additionally runs once a
keystore is added as repo secrets — see the workflow file for exactly which secrets it expects.

You can also trigger a build manually from the repo's Actions tab (workflow_dispatch) with no
code change, useful for regenerating the APK after adding signing secrets for the first time.

## Still to do — wiring blackbox.html to actually use the plugin

Right now this wrapper loads blackbox.html unmodified — the native background-location plugin
is installed and permitted, but nothing in blackbox.html calls it yet. blackbox.html's existing
`navigator.geolocation.watchPosition()` still only works while the screen is on. To actually get
background tracking working end to end, blackbox.html's GPS handling needs a small runtime
branch: detect whether it's running inside this native shell (e.g. check for
`window.Capacitor?.isNativePlatform()`), and if so, start the background-geolocation plugin's
watcher instead of/alongside the browser API, feeding its callbacks into the same
`onPosition()` pipeline blackbox.html already has. This keeps the page working identically as a
plain website for everyone not using the app.
