// modules/metar.js
//
// METAR subsystem: raw-METAR parsing, the METAR side panel, the 3D wind-direction arrow,
// METAR-driven cumulus billboards, and airport-proximity auto-fetch.
//
// This module owns no globals. Everything it needs from the rest of the app (Cesium, the
// viewer, the AIRPORTS table, a couple of geometry helpers, and a way to nudge the radar
// height slider) is passed in via createMetarModule(deps). Call the factory once at startup
// and use the returned API from the rest of the app instead of the old free functions.
//
// Dependencies expected on `deps`:
//   Cesium                          - the Cesium namespace
//   viewer                          - the Cesium Viewer instance
//   AIRPORTS                        - { key: { name, lat, lon, icao, alt, ... }, ... }
//   haversineKm(lat1,lon1,lat2,lon2)
//   destinationPoint(lat,lon,bearingDeg,distKm) -> {lat,lon}
//   headingToWorldVector(position, hdgDeg)      -> normalized Cesium.Cartesian3
//   nearestTerrainHeightM(lat,lon)              -> meters (real terrain, nearest-sample)
//   svgTicks(count,startDeg,endDeg,rOuter,rInner,color,width) -> svg string
//   svgTickLabels(labels,startDeg,endDeg,r,color,size)        -> svg string
//   radarActivePrecipRadiusKm       - number, shared with the radar module
//   onRadarBaseFound(ft)            - optional callback invoked with a cloud base (ft) so the
//                                      radar module can raise its height slider to match
//   metarProxyUrl(icao)             - optional, defaults to `metarProxy.php?ids=${icao}`
//   dom                             - { panel, stationName, body, closeBtn } element refs
//                                      (or a `mount(selector)` helper — see mountDefaultDom below)
//
// Public API returned by createMetarModule(deps):
//   fetchAndShowMetar(airportKey, isRetry?)
//   checkAutoMetarProximity(lat, lon)     - call every tick with the aircraft's position
//   isNearBknOvcAirport(lat, lon)         - used by the sky-appearance code elsewhere
//   clearWindArrow(), clearAirportClouds()
//   closePanel()
//   openTafMate(icao), openLiveAtc(icao), openFltplan(icao)
//   parseRawMetar(raw), describeWx(code), computeHumidityPct(tC, tdC)   - exported for testing/reuse

export function createMetarModule(deps) {
    const {
        Cesium, viewer, AIRPORTS,
        haversineKm, destinationPoint, headingToWorldVector, nearestTerrainHeightM,
        svgTicks, svgTickLabels,
        radarActivePrecipRadiusKm = 10,
        onRadarBaseFound = null,
        metarProxyUrl = (icao) => 'metarProxy.php?ids=' + encodeURIComponent(icao),
    } = deps;

    const dom = deps.dom || mountDefaultDom();

    // ── raw METAR parsing ──────────────────────────────────────────────────
    const CLOUD_COVER_LABELS = { FEW: 'Few', SCT: 'Scattered', BKN: 'Broken', OVC: 'Overcast', VV: 'Vertical Visibility', SKC: 'Sky Clear', CLR: 'Clear', NCD: 'No Cloud Detected', NSC: 'No Significant Cloud' };
    const WX_INTENSITY = { '+': 'Heavy ', '-': 'Light ', 'VC': 'Vicinity ' };
    const WX_DESC = { MI: 'Shallow ', PR: 'Partial ', BC: 'Patches ', DR: 'Low Drifting ', BL: 'Blowing ', SH: 'Showers ', TS: 'Thunderstorm ', FZ: 'Freezing ' };
    const WX_PHENOM = { DZ: 'Drizzle', RA: 'Rain', SN: 'Snow', SG: 'Snow Grains', IC: 'Ice Crystals', PL: 'Ice Pellets', GR: 'Hail', GS: 'Small Hail', UP: 'Unknown Precip', BR: 'Mist', FG: 'Fog', FU: 'Smoke', VA: 'Volcanic Ash', DU: 'Dust', SA: 'Sand', HZ: 'Haze', PY: 'Spray', PO: 'Dust Whirls', SQ: 'Squalls', FC: 'Funnel Cloud', SS: 'Sandstorm', DS: 'Duststorm', NSW: 'No Significant Weather' };

    function describeWx(code) {
        let s = code, intensity = '';
        if (s.startsWith('+')) { intensity = WX_INTENSITY['+']; s = s.slice(1); }
        else if (s.startsWith('-')) { intensity = WX_INTENSITY['-']; s = s.slice(1); }
        else if (s.startsWith('VC')) { intensity = WX_INTENSITY['VC']; s = s.slice(2); }
        let desc = '';
        for (const key of Object.keys(WX_DESC)) { if (s.startsWith(key)) { desc = WX_DESC[key]; s = s.slice(key.length); break; } }
        let phenomStr = '';
        while (s.length >= 2) { const c2 = s.slice(0, 2); phenomStr += (WX_PHENOM[c2] || c2) + ' '; s = s.slice(2); }
        return (intensity + desc + phenomStr).trim() || code;
    }

    function parseRawMetar(raw) {
        const tokens = raw.trim().split(/\s+/);
        const result = { raw, station: tokens[0] || '', wind: null, visibilitySM: null, visibilityM: null, cavok: false, weather: [], clouds: [], tempC: null, dewC: null, altimeterInHg: null, altimeterHpa: null, slpHpa: null };
        let i = 1;
        if (tokens[i] && /^\d{6}Z$/.test(tokens[i])) i++;
        // AUTO/COR are the standard correction/auto markers; Canadian METARs also use CCA/CCB/CCC/etc.
        while (tokens[i] === 'AUTO' || tokens[i] === 'COR' || /^CC[A-Z]?$/.test(tokens[i])) i++;
        if (tokens[i] && /^(\d{3}|VRB)\d{2,3}(G\d{2,3})?KT$/.test(tokens[i])) {
            const m = tokens[i].match(/^(\d{3}|VRB)(\d{2,3})(G(\d{2,3}))?KT$/);
            result.wind = { dir: m[1] === 'VRB' ? null : parseInt(m[1], 10), variable: m[1] === 'VRB', speedKt: parseInt(m[2], 10), gustKt: m[4] ? parseInt(m[4], 10) : null };
            i++;
            if (tokens[i] && /^\d{3}V\d{3}$/.test(tokens[i])) i++;
        }
        if (tokens[i] === 'CAVOK') { result.cavok = true; result.visibilitySM = 6; i++; }
        else {
            if (tokens[i] && /^\d+SM$/.test(tokens[i])) { result.visibilitySM = parseInt(tokens[i], 10); i++; }
            else if (tokens[i] && /^\d\/\dSM$/.test(tokens[i])) { const m = tokens[i].match(/^(\d)\/(\d)SM$/); result.visibilitySM = parseFloat(m[1]) / parseFloat(m[2]); i++; }
            else if (tokens[i] && /^\d{4}$/.test(tokens[i])) { result.visibilityM = parseInt(tokens[i], 10); i++; }
            const wxRe = /^(\+|-|VC)?(MI|PR|BC|DR|BL|SH|TS|FZ)?(DZ|RA|SN|SG|IC|PL|GR|GS|UP|BR|FG|FU|VA|DU|SA|HZ|PY|PO|SQ|FC|SS|DS|NSW)+$/;
            while (tokens[i] && (wxRe.test(tokens[i]) || /^R\d{2}[LRC]?\/.+$/.test(tokens[i]))) {
                if (!/^R\d{2}/.test(tokens[i])) result.weather.push(tokens[i]);
                i++;
            }
            const cloudRe = /^(FEW|SCT|BKN|OVC|VV)(\d{3})(CB|TCU)?$/;
            while (tokens[i] && (cloudRe.test(tokens[i]) || ['SKC', 'CLR', 'NCD', 'NSC'].includes(tokens[i]))) {
                if (cloudRe.test(tokens[i])) { const m = tokens[i].match(cloudRe); result.clouds.push({ cover: m[1], baseFt: parseInt(m[2], 10) * 100, type: m[3] || null }); }
                else result.clouds.push({ cover: tokens[i], baseFt: null, type: null });
                i++;
            }
        }
        if (tokens[i] && /^M?\d{2}\/M?\d{2}$/.test(tokens[i])) {
            const m = tokens[i].match(/^(M?\d{2})\/(M?\d{2})$/);
            result.tempC = parseInt(m[1].replace('M', '-'), 10);
            result.dewC = parseInt(m[2].replace('M', '-'), 10);
            i++;
        }
        if (tokens[i] && /^A\d{4}$/.test(tokens[i])) { result.altimeterInHg = parseInt(tokens[i].slice(1), 10) / 100; i++; }
        else if (tokens[i] && /^Q\d{4}$/.test(tokens[i])) { result.altimeterHpa = parseInt(tokens[i].slice(1), 10); result.altimeterInHg = result.altimeterHpa * 0.0295299831; i++; }
        const slpM = raw.match(/\sSLP(\d{3})\b/);
        if (slpM) { const rawv = parseInt(slpM[1], 10), val = rawv / 10; result.slpHpa = rawv >= 550 ? 900 + val : 1000 + val; }
        return result;
    }

    function computeHumidityPct(tC, tdC) {
        if (typeof tC !== 'number' || typeof tdC !== 'number') return null;
        const es = t => 6.1094 * Math.exp((17.625 * t) / (t + 243.04));
        return Math.round(100 * (es(tdC) / es(tC)));
    }

    // ── 3D wind-direction arrow ────────────────────────────────────────────
    let windArrowEntity = null, windArrowLabelEntity = null;
    const WIND_ARROW_HEAD_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">' +
        '<polygon points="32,4 54,50 32,38 10,50" fill="#7ee787" stroke="#0d1117" stroke-width="3" stroke-linejoin="round"/>' +
        '</svg>';
    const WIND_ARROW_HEAD_DATA_URI = 'data:image/svg+xml;base64,' + btoa(WIND_ARROW_HEAD_SVG);

    function clearWindArrow() {
        if (windArrowEntity) { viewer.entities.remove(windArrowEntity); windArrowEntity = null; }
        if (windArrowLabelEntity) { viewer.entities.remove(windArrowLabelEntity); windArrowLabelEntity = null; }
    }
    function updateAirportWindArrow(airport, m) {
        clearWindArrow();
        if (!m.wind || m.wind.variable || typeof m.wind.dir !== 'number') return;
        const groundM = nearestTerrainHeightM(airport.lat, airport.lon) + 50;
        const pos = Cesium.Cartesian3.fromDegrees(airport.lon, airport.lat, groundM);
        const toBearing = (m.wind.dir + 180) % 360; // "from" -> "blowing toward"
        const headingAxis = headingToWorldVector(pos, toBearing);
        windArrowEntity = viewer.entities.add({
            position: pos,
            billboard: {
                image: WIND_ARROW_HEAD_DATA_URI,
                width: 34, height: 34,
                alignedAxis: headingAxis,
                verticalOrigin: Cesium.VerticalOrigin.CENTER,
                pixelOffset: new Cesium.Cartesian2(0, -70),
                disableDepthTestDistance: Number.POSITIVE_INFINITY
            }
        });
        const speedText = m.wind.speedKt + ' kt' + (m.wind.gustKt ? (' G' + m.wind.gustKt) : '') + '  ' + Math.round(m.wind.dir) + '°';
        windArrowLabelEntity = viewer.entities.add({
            position: pos,
            label: {
                text: speedText, font: 'bold 15px monospace',
                fillColor: Cesium.Color.fromCssColorString('#7ee787'),
                outlineColor: Cesium.Color.BLACK, outlineWidth: 3,
                style: Cesium.LabelStyle.FILL_AND_OUTLINE,
                pixelOffset: new Cesium.Cartesian2(0, -100),
                disableDepthTestDistance: Number.POSITIVE_INFINITY
            }
        });
    }

    // ── METAR-driven cumulus billboards ────────────────────────────────────
    let cloudCollection = null;
    const CLOUD_RADIUS_SM = 5; // METARs are considered representative within ~5SM of the station
    const CLOUD_RADIUS_KM = CLOUD_RADIUS_SM * 1.60934;
    const CLOUD_COVER_DENSITY = { FEW: 4, SCT: 8, BKN: 14, OVC: 20 };
    const CLOUD_ALPHA_MIN = 0.35, CLOUD_ALPHA_MAX = 0.55;
    function ensureCloudCollection() {
        if (!cloudCollection) cloudCollection = viewer.scene.primitives.add(new Cesium.CloudCollection());
        return cloudCollection;
    }
    function clearAirportClouds() { if (cloudCollection) cloudCollection.removeAll(); }
    function spawnCloudsForLayer(airport, groundM, cover, baseFt) {
        const count = CLOUD_COVER_DENSITY[cover]; if (!count) return; // skip SKC/CLR/NCD/NSC/VV
        const collection = ensureCloudCollection();
        const heightM = groundM + (typeof baseFt === 'number' ? baseFt : 3000) * 0.3048;
        for (let i = 0; i < count; i++) {
            const bearing = Math.random() * 360, distKm = Math.random() * CLOUD_RADIUS_KM;
            const dest = destinationPoint(airport.lat, airport.lon, bearing, distKm);
            const pos = Cesium.Cartesian3.fromDegrees(dest.lon, dest.lat, heightM);
            const w = 400 + Math.random() * 500, h = 150 + Math.random() * 150, d = 400 + Math.random() * 500;
            const alpha = CLOUD_ALPHA_MIN + Math.random() * (CLOUD_ALPHA_MAX - CLOUD_ALPHA_MIN);
            collection.add({ position: pos, maximumSize: new Cesium.Cartesian3(w, h, d), slice: -1, brightness: 0.9 + Math.random() * 0.2, color: Cesium.Color.WHITE.withAlpha(alpha) });
        }
    }
    function updateAirportClouds(airport, m) {
        clearAirportClouds();
        if (!m.clouds.length) return;
        const groundM = nearestTerrainHeightM(airport.lat, airport.lon);
        for (const c of m.clouds) spawnCloudsForLayer(airport, groundM, c.cover, c.baseFt);
    }
    function lowestSignificantCloudBaseFt(m) {
        let lowest = null;
        for (const c of m.clouds) {
            if (c.cover !== 'SCT' && c.cover !== 'BKN' && c.cover !== 'OVC') continue;
            if (typeof c.baseFt !== 'number') continue;
            if (lowest === null || c.baseFt < lowest) lowest = c.baseFt;
        }
        return lowest;
    }

    // ── sky-appearance proximity flag (consumed by the sky/radar module) ──
    let metarCloudFlagByAirport = new Map(); // airportKey -> true if last-loaded METAR had BKN/OVC
    function metarHasBknOvc(m) { return m.clouds.some(c => c.cover === 'BKN' || c.cover === 'OVC'); }
    function isNearBknOvcAirport(lat, lon) {
        if (typeof lat !== 'number' || typeof lon !== 'number') return false;
        for (const [key, cloudy] of metarCloudFlagByAirport) {
            if (!cloudy) continue;
            const ap = AIRPORTS[key]; if (!ap) continue;
            if (haversineKm(lat, lon, ap.lat, ap.lon) <= radarActivePrecipRadiusKm) return true;
        }
        return false;
    }

    // ── auto-fetch when the aircraft enters an airport's ~5SM zone ────────
    let autoMetarCurrentAirportKey = null;
    let autoMetarLastCheckMs = 0;
    const AUTO_METAR_CHECK_INTERVAL_MS = 3000;
    function checkAutoMetarProximity(lat, lon) {
        if (typeof lat !== 'number' || typeof lon !== 'number') return;
        const now = Date.now();
        if (now - autoMetarLastCheckMs < AUTO_METAR_CHECK_INTERVAL_MS) return;
        autoMetarLastCheckMs = now;
        let nearestKey = null, nearestDist = Infinity;
        for (const key in AIRPORTS) {
            const ap = AIRPORTS[key];
            if (!ap.icao) continue;
            const d = haversineKm(lat, lon, ap.lat, ap.lon);
            if (d <= CLOUD_RADIUS_KM && d < nearestDist) { nearestDist = d; nearestKey = key; }
        }
        if (nearestKey === autoMetarCurrentAirportKey) return;
        const previousKey = autoMetarCurrentAirportKey;
        autoMetarCurrentAirportKey = nearestKey;
        if (nearestKey) fetchAndShowMetar(nearestKey);
        else if (previousKey) {
            closePanel();
            clearWindArrow();
            clearAirportClouds();
            metarCloudFlagByAirport.delete(previousKey);
        }
    }

    // ── panel rendering ────────────────────────────────────────────────────
    function closePanel() { dom.panel.style.display = 'none'; }
    function panelSetLoading(name) {
        dom.stationName.textContent = name;
        dom.body.innerHTML = '<div style="padding:6px; color:var(--text-muted); font-size:11px;">Loading METAR…</div>';
        dom.panel.style.display = 'flex';
    }
    function panelSetError(name, msgHtml) {
        dom.stationName.textContent = name;
        dom.body.innerHTML = `<div style="padding:6px; color:#ff7b72; font-size:11px;">${msgHtml}</div>`;
        dom.panel.style.display = 'flex';
        clearWindArrow();
        clearAirportClouds();
        const apKeyForFlag = Object.keys(AIRPORTS).find(k => AIRPORTS[k].name === name || (AIRPORTS[k].icao && name.indexOf(AIRPORTS[k].icao) === 0));
        if (apKeyForFlag) metarCloudFlagByAirport.delete(apKeyForFlag);
    }
    function renderMetarPanel(airport, m) {
        dom.stationName.textContent = (m.station || airport.icao) + ' — ' + airport.name;
        const apKeyForFlag = Object.keys(AIRPORTS).find(k => AIRPORTS[k] === airport);
        if (apKeyForFlag) metarCloudFlagByAirport.set(apKeyForFlag, metarHasBknOvc(m));
        const humidity = computeHumidityPct(m.tempC, m.dewC);
        const windDirText = m.wind ? (m.wind.variable ? 'Variable' : (m.wind.dir + '°')) : '—';
        const windSpdText = m.wind ? (m.wind.speedKt + ' kt' + (m.wind.gustKt ? (' gusting ' + m.wind.gustKt + ' kt') : '')) : 'Calm';
        const rotationDeg = (m.wind && !m.wind.variable) ? m.wind.dir : 0;
        const compassSvg = `<svg viewBox="0 0 100 100" width="90" height="90">
            <circle cx="50" cy="50" r="46" fill="#0d1117" stroke="#30363d" stroke-width="2"/>
            ${svgTicks(35, 0, 360, 46, 41, '#8b949e', 1)}${svgTickLabels(['N', 'E', 'S', 'W'], 0, 270, 32, '#58a6ff', 8)}
            ${(m.wind && !m.wind.variable) ? `<g style="transform-origin:50px 50px; transform:rotate(${rotationDeg}deg);"><line x1="50" y1="14" x2="50" y2="50" stroke="#f0c419" stroke-width="3"/><polygon points="50,8 45,18 55,18" fill="#f0c419"/></g>` : `<text x="50" y="54" text-anchor="middle" font-size="10" fill="#8b949e">CALM/VRB</text>`}
            </svg>`;
        const cloudsHtml = m.clouds.length ? m.clouds.map(c => `<div>${CLOUD_COVER_LABELS[c.cover] || c.cover}${c.baseFt !== null ? ' @ ' + c.baseFt.toLocaleString() + 'ft' : ''}${c.type ? ' (' + c.type + ')' : ''}</div>`).join('') : `<div style="color:var(--text-muted);">${m.cavok ? 'CAVOK — no significant cloud' : 'No cloud data'}</div>`;
        const wxHtml = m.weather.length ? m.weather.map(w => `<div>${describeWx(w)}</div>`).join('') : '<div style="color:var(--text-muted);">No significant weather</div>';
        const altimeterText = (typeof m.altimeterInHg === 'number') ? m.altimeterInHg.toFixed(2) + ' inHg' : '—';
        const slpText = (typeof m.slpHpa === 'number') ? m.slpHpa.toFixed(1) + ' hPa' : (m.altimeterHpa ? m.altimeterHpa + ' hPa (QNH)' : '—');
        dom.body.innerHTML = `
            <div style="display:flex; gap:14px; align-items:center; flex-wrap:wrap;">
                <div style="text-align:center;">${compassSvg}<div style="font-size:10px; color:var(--text-muted);">${windDirText} · ${windSpdText}</div></div>
                <div style="flex:1; min-width:140px; font-size:11px; line-height:1.7;">
                    <div>Temp: ${typeof m.tempC === 'number' ? m.tempC + '°C' : '—'} · Dewpoint: ${typeof m.dewC === 'number' ? m.dewC + '°C' : '—'}</div>
                    <div>Humidity (calc): ${humidity !== null ? humidity + '%' : '—'}</div>
                    <div>Altimeter: ${altimeterText}</div>
                    <div>SLP: ${slpText}</div>
                    <div>Visibility: ${m.visibilitySM ? m.visibilitySM + ' SM' : (m.visibilityM ? m.visibilityM + ' m' : '—')}</div>
                </div>
            </div>
            <div style="margin-top:10px; font-size:11px;"><b style="color:var(--accent);">Clouds</b>${cloudsHtml}</div>
            <div style="margin-top:6px; font-size:11px;"><b style="color:var(--accent);">Weather</b>${wxHtml}</div>
            <div style="margin-top:8px; font-size:9px; color:var(--text-muted); word-break:break-all;">${m.raw}</div>
            <div style="margin-top:10px; text-align:right; display:flex; gap:6px; justify-content:flex-end;">
                <button data-metar-action="atc" data-icao="${(airport.icao || '')}" style="background:var(--bg-light); color:var(--accent); border:1px solid var(--border); border-radius:5px; padding:5px 10px; font-family:'Courier New',monospace; font-size:11px; cursor:pointer;">▶ Play ATC</button>
                <button data-metar-action="fltplan" data-icao="${(airport.icao || '')}" style="background:var(--bg-light); color:var(--accent); border:1px solid var(--border); border-radius:5px; padding:5px 10px; font-family:'Courier New',monospace; font-size:11px; cursor:pointer;">AP Info</button>
                <button data-metar-action="taf" data-icao="${(m.station || airport.icao || '')}" style="background:var(--bg-light); color:var(--accent); border:1px solid var(--border); border-radius:5px; padding:5px 10px; font-family:'Courier New',monospace; font-size:11px; cursor:pointer;">📋 See TAF</button>
            </div>`;
        // Delegate the three action buttons instead of inline onclick="" (avoids fighting a
        // strict CSP and avoids re-serializing the ICAO through an HTML attribute).
        dom.body.querySelectorAll('[data-metar-action]').forEach(btn => {
            btn.addEventListener('click', () => {
                const icao = btn.getAttribute('data-icao');
                const action = btn.getAttribute('data-metar-action');
                if (action === 'atc') openLiveAtc(icao);
                else if (action === 'fltplan') openFltplan(icao);
                else if (action === 'taf') openTafMate(icao);
            });
        });
        dom.panel.style.display = 'flex';
        updateAirportWindArrow(airport, m);
        updateAirportClouds(airport, m);
        const radarBaseFt = lowestSignificantCloudBaseFt(m);
        if (radarBaseFt !== null && typeof onRadarBaseFound === 'function') onRadarBaseFound(radarBaseFt);
    }

    function looksLikeHtmlErrorPage(text) {
        const t = text.trim();
        return /^</.test(t) || /<html/i.test(t) || /<head/i.test(t) || /<body/i.test(t) || /server hangup/i.test(t);
    }

    async function fetchAndShowMetar(airportKey, isRetry) {
        const airport = AIRPORTS[airportKey]; if (!airport) return;
        if (!airport.icao) { panelSetError(airport.name, 'No METAR station available for this airport.'); return; }
        panelSetLoading(airport.name);
        let text, httpOk = true;
        try { const res = await fetch(metarProxyUrl(airport.icao), { cache: 'no-store' }); httpOk = res.ok; text = await res.text(); }
        catch (e) { panelSetError(airport.name, 'Failed to fetch METAR.'); return; }
        if (!httpOk || looksLikeHtmlErrorPage(text || '')) {
            if (!isRetry) {
                panelSetLoading(airport.name + ' (retrying…)');
                await new Promise(r => setTimeout(r, 1500));
                return fetchAndShowMetar(airportKey, true);
            }
            panelSetError(airport.name, 'METAR service temporarily unavailable (proxy/upstream error) — try again in a moment.');
            return;
        }
        if (!text || !text.trim()) { panelSetError(airport.name, 'Empty response from METAR proxy.'); return; }
        if (/^ERROR:/im.test(text.trim())) { panelSetError(airport.name, text.trim()); return; }
        const reportSegments = text.split(/\bMETAR\b\s+/i).map(s => s.trim()).filter(Boolean);
        const wantedIcao = airport.icao.toUpperCase();
        const stationRe = new RegExp('^' + wantedIcao + '\\b', 'i');
        const matchedSegment = reportSegments.find(seg => stationRe.test(seg));
        if (!matchedSegment) {
            const snippet = text.trim().split(/\r?\n/).slice(0, 6).join('\n');
            panelSetError(airport.name, 'No recent METAR found for ' + airport.icao + '.<div style="margin-top:6px; font-size:9px; color:var(--text-muted); white-space:pre-wrap; word-break:break-all;">' + snippet.replace(/[<>&]/g, c => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;' }[c])) + '</div>');
            return;
        }
        const singleLineMatch = matchedSegment.match(/^[^\r\n]*/);
        renderMetarPanel(airport, parseRawMetar((singleLineMatch ? singleLineMatch[0] : matchedSegment).trim()));
    }

    // ── external links ──────────────────────────────────────────────────────
    function openTafMate(icao) {
        if (!icao) return;
        const upper = icao.toUpperCase();
        const url = 'TAFmate.html?icao=' + encodeURIComponent(upper);
        const win = window.open(url, '_blank', 'noopener,noreferrer');
        if (!win) console.warn('[metar] openTafMate: popup blocked');
        try { localStorage.setItem('tafmate_pending_icao', upper); } catch (e) { /* ignore */ }
    }
    function openLiveAtc(icao) {
        if (!icao) return;
        window.open('https://www.liveatc.net/search/?icao=' + encodeURIComponent(icao.toUpperCase()), '_blank', 'noopener,noreferrer');
    }
    function openFltplan(icao) {
        if (!icao) return;
        const url = 'https://www.fltplan.com/AwMainSearchToAirportID.exe?CRN10=1&CARRYUNAME=PILOT&MODE=search&AIRPORTSEL='
            + encodeURIComponent(icao.toUpperCase()) + '&SIZEFLAG=BIG&WINDOW=YES';
        window.open(url, '_blank', 'noopener,noreferrer');
    }

    dom.closeBtn.addEventListener('click', closePanel);

    return {
        fetchAndShowMetar,
        checkAutoMetarProximity,
        isNearBknOvcAirport,
        clearWindArrow,
        clearAirportClouds,
        closePanel,
        openTafMate, openLiveAtc, openFltplan,
        // exported for unit testing / reuse elsewhere
        parseRawMetar, describeWx, computeHumidityPct,
    };
}

// Looks up the panel elements already in the page (see partials/metar-panel.html) instead of
// requiring every caller to pass `dom` by hand.
export function mountDefaultDom() {
    return {
        panel: document.getElementById('metarPanel'),
        stationName: document.getElementById('metarStationName'),
        body: document.getElementById('metarBody'),
        closeBtn: document.getElementById('metarPanelCloseBtn'),
    };
}
