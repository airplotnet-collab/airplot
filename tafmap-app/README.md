# TAF Map - Android app (Capacitor shell)

**TAF Map – METAR & TAF**: METARs and TAFs on one map with a time slider, plus SIGMETs, an automated weather
chart (isobars, highs and lows) and cloud / visibility areas drawn from the reports.

A thin native Android shell around the live page **https://airplot.net/wxmap.html** (`capacitor.config.json`
→ `server.url`). Like `android-app/` (Blackbox), the page itself stays on GoDaddy and is edited there as usual:
the app always loads what is on airplot.net, so ordinary page changes need **no** rebuild.

- Package: `net.airplot.tafmap`
- Plugin: `@capawesome/capacitor-google-sign-in` - Google blocks its web sign-in inside app WebViews, so
  `modules/airplot-member.js` uses this plugin when it runs in the app (same member-auth.php after that).
- In the app, `wxmap.html` hides the "★ Membership" and "Support me" links (Google Play allows payments
  for digital features only through Play Billing). Members just sign in.

## Building (GitHub Actions)

| Workflow | When | Output |
|---|---|---|
| `build-tafmap-android.yml` | push to `main` touching `tafmap-app/**`, or by hand | debug APK (artifact + Release `tafmap-latest`) |
| `build-tafmap-play.yml` | by hand only | signed `.aab` for Play Console (same `PLAY_*` upload-key secrets as Blackbox) |

## One-time setup

1. **Google Cloud Console** (the project of web client `537372556637-…`): Credentials → Create OAuth client
   ID → **Android**, package `net.airplot.tafmap`, SHA-1 = the one the debug build prints. Make a second one
   with the **App signing key SHA-1** from Play Console (Setup → App integrity) once the app exists there.
2. **Play Console**: Create app "TAF Map" (app, free), upload the `.aab` from `build-tafmap-play.yml` to
   Internal testing first. Store icon: `store/play-icon-512.png`.
