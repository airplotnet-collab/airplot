"""
(2026-11-15/16, CN) Visibility estimated from the ECMWF fields themselves - no GFS (another model's weather does not
line up with ECMWF's rain, cloud and winds). A forecaster's short-range rules on the 2 m relative humidity (from T and
Td), the 10 m wind and the precipitation, the thresholds checked against 759 METARs in North America, Europe, East Asia
and Australia (check_vis.py, run 18Z 15 Nov 2026):

  fog / mist - 2 m RH and the 10 m wind
    RH >= 94 %:  wind < 5 kt   -> fog: 1/4 SM (RH >= 97 %), else 1/2 SM
                 wind 5-10 kt  -> fog, may be lifting: 1 SM
                 wind >= 10 kt -> mist: 4 SM
    RH >= 91 %, wind < 10 kt  -> mist: 4 SM
    (In real air a 2 C spread means fog and 3 C mist. ECMWF's 2 m air reads that moist - about 85-88 % RH - on most
    clear nights, so fog in this model needs about 94 %: half the false fog of a spread rule, the same hits.)
  precipitation - the 3 h total / 3, mm/h of water (3-hour averages hide the showers, so the steps are low)
    rain:  >= 0.5 mm/h -> mist in rain: 5 SM;  >= 2 mm/h -> heavy: 3 SM
    snow:  light (< 0.5) 6 SM; moderate (0.5 - 1.5) 5 -> 3 SM; heavy (> 1.5) 2 -> 1 SM (1 SM from 3 mm/h)
  visibility = the lowest of these; nothing applies -> 10 SM (shown as 6+)
Check: CSI 0.27 for <= 5 SM (GFS 0.13, spread < 2 C 0.23), 0.18 for < 3 SM (GFS 0.07).
"""
import numpy as np

FOG_RH, MIST_RH, DENSE_RH = 94.0, 91.0, 97.0
FOG_CALM, FOG_LIFT = 5.0, 10.0
RAIN_BR, RAIN_HEAVY = 0.5, 2.0

def rh_pct(t2, td2):
    es = lambda x: np.exp(17.625 * x / (243.04 + x))
    return 100.0 * es(td2) / es(t2)

def vis_sm(t2, td2, wind_kt, tp_3h, sf_3h):
    t2, td2, w = np.asarray(t2, np.float32), np.asarray(td2, np.float32), np.asarray(wind_kt, np.float32)
    tp = np.nan_to_num(np.asarray(tp_3h, np.float32)); sf = np.nan_to_num(np.asarray(sf_3h, np.float32))
    rh = rh_pct(t2, td2)
    v = np.full(np.broadcast(rh, w).shape, 10.0, np.float32)
    fog = rh >= FOG_RH
    v = np.where(fog & (w < FOG_CALM), np.where(rh >= DENSE_RH, 0.25, 0.5), v)
    v = np.where(fog & (w >= FOG_CALM) & (w < FOG_LIFT), 1.0, v)
    v = np.where(fog & (w >= FOG_LIFT), 4.0, v)
    v = np.where(~fog & (rh >= MIST_RH) & (w < FOG_LIFT), np.minimum(v, 4.0), v)
    rain = np.maximum(0, tp - sf) / 3.0; snow = sf / 3.0
    v = np.minimum(v, np.where(rain >= RAIN_HEAVY, 3.0, np.where(rain >= RAIN_BR, 5.0, 10.0)))
    sv = np.where(snow >= 3, 1.0, np.where(snow > 1.5, 2.0 - (snow - 1.5) / 1.5, np.where(snow >= 0.5, 5.0 - (snow - 0.5) * 2.0, np.where(snow > 0.05, 6.0, 10.0))))
    v = np.minimum(v, sv)
    return np.where(np.isnan(rh), np.nan, v).astype(np.float32)
