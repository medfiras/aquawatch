"""Constants for the AquaWatch integration."""

DOMAIN = "aquawatch"

CONF_PROVIDER = "provider"
CONF_CONTRACT_ID = "contract_id"
CONF_EMAIL = "email"
CONF_PASSWORD = "password"

DEFAULT_UPDATE_INTERVAL_HOURS = 24
MIN_UPDATE_INTERVAL_HOURS = 1
MAX_UPDATE_INTERVAL_HOURS = 24

OPT_UPDATE_INTERVAL_HOURS = "update_interval_hours"
OPT_LEAK_THRESHOLD_RATIO = "leak_threshold_ratio"
OPT_LEAK_CONSECUTIVE_DAYS = "leak_consecutive_days"
OPT_ANOMALY_ZSCORE_THRESHOLD = "anomaly_zscore_threshold"
OPT_BUDGET_AMOUNT = "budget_amount"
OPT_BUDGET_UNIT = "budget_unit"
OPT_HOUSEHOLD_SIZE = "household_size"

BUDGET_UNIT_EUR = "eur"
BUDGET_UNIT_M3 = "m3"

DEFAULT_LEAK_THRESHOLD_RATIO = 1.5
DEFAULT_LEAK_CONSECUTIVE_DAYS = 2
DEFAULT_ANOMALY_ZSCORE_THRESHOLD = 2.5
DEFAULT_HOUSEHOLD_SIZE = 1

EVENT_LEAK_SUSPECTED = "aquawatch_fuite_suspectee"
EVENT_ANOMALY_DETECTED = "aquawatch_anomalie_detectee"
EVENT_BUDGET_EXCEEDED = "aquawatch_budget_depasse"

SERVICE_FORCE_REFRESH = "force_refresh"
SERVICE_EXPORT_CSV = "export_csv"
SERVICE_RECALIBRATE_BASELINE = "recalibrate_baseline"
