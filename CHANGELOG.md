# Changelog

## 1.1.0 - 2026-11-18

Forecast accuracy (the day forecast was largely overestimated on overcast days and drifted upwards during the day).

- **Today = produced so far + remaining.** Past hours of the day are no longer re-forecast from the latest weather run,
  so the evening value equals the measured production.
- **Intraday correction.** The remaining forecast and the current-hour power are multiplied by the measured / forecast
  ratio of the last 3 completed hours of the day (bounded 0.3 to 1.5, fading towards 1 with a 3 h half-life).
  The ratio is exposed as the `intraday_correction` attribute of the *Forecast today* sensor, next to `produced`.
- **Calibration per sky class.** Open-Meteo `cloud_cover` is now downloaded. Each hour learns a factor for clear (< 30 %),
  variable (30 to 70 %) and overcast (> 70 %) skies, blended with the existing hour factor until the class has 7 learned
  days. The existing calibration is kept and used as the starting point.
- **Weather model comparison.** Once a day after 18:00, tomorrow's total is also computed with Météo-France, ICON and
  ECMWF (same calibration). At midnight the log line "Weather model comparison" gives each model's error, and the running
  error and bias per model are in the diagnostics (`forecast.models`).
- Day-ahead accuracy (*Forecast error* sensor) is unchanged.

## 1.0.2

- `tsun_microinverters.import_homey` service.
