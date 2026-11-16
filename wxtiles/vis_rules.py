"""
(2026-11-15, CN) Visibility estimated from the ECMWF fields themselves (no GFS: another model's weather does not
match ECMWF's rain, cloud and winds). Short-range forecaster rules:

  fog / mist from the dew-point spread (T - Td) and the 10 m wind
    spread < 2 C:  wind < 5 kt  -> fog        (spread < 1: 1/4 SM, else 1/2 SM)
                   wind < 10 kt -> fog, may be lifting (1 SM)
                   stronger     -> mist (5 SM)
    spread < 4 C:  wind < 10 kt -> mist (4 SM); stronger -> mist (6 SM)
  precipitation (rate = the 3 h total / 3, water mm/h)
    snow:  light  (< 0.5)        6 SM
           moderate (0.5 - 1.5)  5 -> 3 SM
           heavy (> 1.5)         2 -> 1 SM (1 SM from 3 mm/h)
    rain:  moderate (1 - 4)      mist, 5 SM
           heavy (>= 4)          3 SM
  visibility = the lowest of these; nothing applies -> 10 SM ("6+")
"""
import numpy as np

def vis_sm(t2, td2, wind_kt, tp_3h, sf_3h):
    t2, td2, w = np.asarray(t2, float), np.asarray(td2, float), np.asarray(wind_kt, float)
    tp = np.nan_to_num(np.asarray(tp_3h, float)); sf = np.nan_to_num(np.asarray(sf_3h, float))
    sp = t2 - td2
    v = np.full(np.broadcast(t2, td2, w).shape, 10.0)
    # fog / mist
    fog = sp < 2
    v = np.where(fog & (w < 5), np.where(sp < 1, 0.25, 0.5), v)
    v = np.where(fog & (w >= 5) & (w < 10), np.minimum(v, 1.0), v)
    v = np.where(fog & (w >= 10), np.minimum(v, 5.0), v)
    mist = (sp >= 2) & (sp < 4)
    v = np.where(mist & (w < 10), np.minimum(v, 4.0), v)
    v = np.where(mist & (w >= 10), np.minimum(v, 6.0), v)
    # precipitation, mm/h (water)
    snow = sf / 3.0; rain = np.maximum(0, tp - sf) / 3.0
    sv = np.where(snow >= 3, 1.0, np.where(snow > 1.5, 2.0 - (snow - 1.5) / 1.5, np.where(snow >= 0.5, 5.0 - (snow - 0.5) * 2.0, np.where(snow > 0.05, 6.0, 10.0))))
    rv = np.where(rain >= 4, 3.0, np.where(rain >= 1, 5.0, 10.0))
    v = np.minimum(v, np.minimum(sv, rv))
    return np.where(np.isnan(sp), np.nan, v)
