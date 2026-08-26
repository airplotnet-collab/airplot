# Wiring modules/metar.js into aircraft3d.html

Repo layout after this step:

```
airplot/
├── aircraft3d.html
├── modules/
│   └── metar.js
└── partials/
    └── metar-panel.html   (reference only — panel markup already exists inline in aircraft3d.html)
```

`aircraft3d.html` stays a single file for now (deploy simplicity) but loads `metar.js` as an
ES module and deletes the inline METAR code it replaces. Four patches:

## 1. Load the module

Find:
```html
<script type="module">
  import tzlookup from 'https://esm.sh/tz-lookup@6.1.25';
  window.__tzLookupFn = tzlookup;
</script>
```

Replace:
```html
<script type="module">
  import tzlookup from 'https://esm.sh/tz-lookup@6.1.25';
  window.__tzLookupFn = tzlookup;
</script>
<script type="module" src="./modules/metar.js"></script>
```

(The panel markup — `<div id="metarPanel">…</div>` — is unchanged and stays exactly where it
is; `partials/metar-panel.html` is just a reference copy for future full modularization.)

## 2. Remove the inline METAR code

Delete the whole block from the `// ── raw METAR parsing ──` comment down through the
`openFltplan()` function — everything metar.js now owns. In the current file that's the span
from:

```
const CLOUD_COVER_LABELS = { FEW:'Few', ...
```

down through:

```
function openFltplan(icao) {
    ...
}
```

(Leave `addAirportMarkers()` / `addAirportCompasses()` and everything after them untouched —
those are airport-pin rendering, not METAR-specific.)

## 3. Create the module instance once the main script runs

Find (right after `applyAirportType('c172');` — i.e. once `viewer`, `AIRPORTS`,
`haversineKm`, `destinationPoint`, `headingToWorldVector`, `nearestTerrainHeightM`,
`svgTicks`, `svgTickLabels`, and `RADAR_ACTIVE_PRECIP_RADIUS_KM` all exist):

```
applyAircraftType('c172'); // sane default before the user picks anything at the startup overlay
```

Replace:
```
applyAircraftType('c172'); // sane default before the user picks anything at the startup overlay

const metarModule = await import('./modules/metar.js').then(m => m.createMetarModule({
    Cesium, viewer, AIRPORTS,
    haversineKm, destinationPoint, headingToWorldVector, nearestTerrainHeightM,
    svgTicks, svgTickLabels,
    radarActivePrecipRadiusKm: RADAR_ACTIVE_PRECIP_RADIUS_KM,
    onRadarBaseFound: setRadarHeightFt,
}));
const { fetchAndShowMetar, checkAutoMetarProximity, isNearBknOvcAirport } = metarModule;
```

> Note: the surrounding `<script>` block is a classic script, not `type="module"`, so `await
> import(...)` at top level needs wrapping in an async IIFE, or switch that whole block to
> `type="module"` (recommended long-term — see the follow-up modules planned below). Quick
> fix for now:
>
> ```js
> let metarModule;
> (async () => {
>   metarModule = await import('./modules/metar.js').then(m => m.createMetarModule({ ... }));
>   window.fetchAndShowMetar = metarModule.fetchAndShowMetar;
>   window.checkAutoMetarProximity = metarModule.checkAutoMetarProximity;
>   window.isNearBknOvcAirport = metarModule.isNearBknOvcAirport;
> })();
> ```
> and call the three through `window.` at existing call sites until the whole file is ES
> modules (see step 4 below for why `isNearBknOvcAirport`/`checkAutoMetarProximity` are the
> two call sites that matter most).

## 4. Existing call sites — no change needed if using the `window.` shim above; otherwise:

- `updateSkyAppearance()` calls `isNearBknOvcAirport(lat, lon)` — now `metarModule.isNearBknOvcAirport`.
- `upsertAircraftEntity()` calls `checkAutoMetarProximity(p.lat, p.lon)` — now `metarModule.checkAutoMetarProximity`.
- The turntable click handler calls `fetchAndShowMetar(picked.id.id.slice(14))` — now `metarModule.fetchAndShowMetar`.
- `document.getElementById('metarPanelCloseBtn')` listener is now owned by the module itself
  (wired inside `createMetarModule`) — delete the old
  `document.getElementById('metarPanelCloseBtn').addEventListener(...)` line.
- Anywhere the old code did `document.getElementById('metarPanel').style.display = 'none'`
  directly (e.g. `resetAppStateForMainMenu`, `stopLiveTracking` reset, jump-to-location) —
  replace with `metarModule.closePanel()`.

## What did NOT move

`setRadarHeightFt()` stays in the main file (it's radar-module territory) — metar.js just
calls it via the injected `onRadarBaseFound` callback instead of reaching for it directly.
This is the seam to reuse when radar.js is split out next: `onRadarBaseFound` becomes
`radarModule.setRadarHeightFt`.
