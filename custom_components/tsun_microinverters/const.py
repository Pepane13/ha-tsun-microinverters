"""Constants of the TSUN micro-inverters integration."""

DOMAIN = "tsun_microinverters"
PLATFORMS = ["sensor", "binary_sensor", "button"]
CONF_LOGGER_SN = "logger_sn"
CONF_INVERTER_SN = "inverter_serial"
CONF_NOTIFY_SERVICE = "notify_service"
EVENT = f"{DOMAIN}_event"
STORAGE_VERSION = 1
DIAG_BUFFER = 300
DEFAULT_NAME = "TSUN MX3000D"
