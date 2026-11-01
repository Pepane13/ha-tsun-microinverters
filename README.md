# TSUN micro-inverters for Home Assistant

Local, read-only integration for **TSUN 6-in-1 micro-inverters (MX3000D)** through their Solarman V5 Wi-Fi logger (TCP port 8899).
Port of the Homey app `com.pepane13.tsun`: same register map, same monitoring logic, same messages (English and French).

Nothing is ever written to the inverter: only Modbus function 0x03 (read holding registers) is used.

## Features

- **Live values**: AC power, daily and total energy (Energy dashboard ready, total never decreases), grid voltage, current and
  frequency, inverter temperature, status, DC power, DC→AC efficiency, peak of the day; per panel power and daily energy
  (voltage, current and total energy available, disabled by default).
- **Robust polling**: one TCP connection per cycle, short retries on serial-link collisions with the TSUN cloud polling,
  night standby detected after 3 failed cycles (polling every 5 min), unreachable-while-producing detected separately
  (polling every minute), 15-minute start-up grace, daily counters kept until midnight even when the inverter resets them at dusk.
- **Communication monitoring**: response time, success rate over 1 hour, cause of each failure (Wi-Fi, logger busy, wrong serial…),
  alert when the inverter stays unreachable while producing.
- **Grid and inverter checks**: voltage limits, frequency, temperature, efficiency, alarm and fault bits (notified only when they
  last 5 minutes), thermal derating (power drop with a voltage rise on most panels followed by a 5-minute plateau).
- **Panel monitoring**
  - *adaptive* (default): learns each panel's usual share of production per 15-minute slot of solar time, so recurring
    shadows are never reported; each evening, a day verdict compares the best share each panel reached in direct sun with its
    usual best share (notification only for a new anomaly; a change lasting 7 sunny days is accepted as the new normal);
  - *simple*: each panel compared with the average of the others;
  - "not producing" detection in both modes, with a hint from the panel voltage.
- **Production forecast** (optional): hourly irradiance on the panel plane from Open-Meteo (free, no account; location rounded
  to about 1 km), calibrated hour by hour on the measured production; today, remaining, tomorrow, current hour; day-ahead accuracy.
- **Daily summary** in the log at midnight.
- **Diagnostics** download (serial numbers masked) and a *Last diagnostic* sensor with the recent journal.
- **Roof card** (`custom:tsun-roof-card`), loaded automatically: live panels laid out as on the roof.

## Installation

HACS → Integrations → ⋮ → Custom repositories → `https://github.com/Pepane13/ha-tsun-microinverters` (Integration), install,
restart, then *Settings → Devices & services → Add integration → TSUN micro-inverters*.

You need the logger IP address (give it a fixed lease) and the logger serial number (10 digits on its label, not the inverter
serial number). At night the inverter sleeps: tick *Add even if the inverter does not answer* to add it anyway.

## Options

Reading interval, temperature offset, alert delays, detection mode and thresholds, grid limits, forecast (tilt, azimuth as a
compass bearing, peak power, weather model) and notifications (persistent notification always, plus an optional notify service
such as `notify.mobile_app_phone`).

## Events

`tsun_microinverters_event` with `type`: `status_changed`, `comm_lost`, `comm_restored`, `panel_underperforming`,
`panel_recovered`, `panel_not_producing`, `derating`, `forecast_updated` (data as in the Homey flow cards).

```yaml
triggers:
  - trigger: event
    event_type: tsun_microinverters_event
    event_data: { type: panel_not_producing }
```

## Services

- `tsun_microinverters.forecast_window` (response): forecast kWh of `today`/`tomorrow` between `start` and `end`.
- `tsun_microinverters.refresh_forecast`
- `tsun_microinverters.reset_baseline`

## Roof card

```yaml
type: custom:tsun-roof-card
entity: sensor.tsun_mx3000d_power
order: 5,4,2,6,1,3   # panel numbers row by row, as on the roof
columns: 3
panel_max: 500       # W for a full panel
```

## Tests

`pytest -p no:homeassistant tests` (engine against a fake V5 logger, no Home Assistant needed) and
`pytest tests_ha` (with `pytest-homeassistant-custom-component`).
