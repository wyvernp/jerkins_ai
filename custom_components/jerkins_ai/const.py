"""Constants for the Jerkins AI integration."""

DOMAIN = "jerkins_ai"

# Configuration constants
CONF_SENSORS = "sensors"
CONF_ZONE_MAPPINGS = "zone_mappings"
CONF_ACTION_MAPPINGS = "action_mappings"
CONF_POLLING_INTERVAL = "polling_interval"
DEFAULT_POLLING_INTERVAL = 60  # in seconds

# Defaults
DEFAULT_NAME = "Jerkins AI"

# Config flow step IDs
STEP_USER = "user"
STEP_SENSORS = "sensors"
STEP_ZONES = "zones"
STEP_ACTIONS = "actions"
STEP_LLM = "llm"

# Service discovery
SUPPORTED_DOMAINS = ["light", "switch", "climate", "cover", "media_player", "fan", "automation", "script", "binary_sensor"]
SERVICE_CATEGORY_TOGGLE = ["light", "switch", "fan", "input_boolean"]
SERVICE_CATEGORY_COVER = ["cover"]
SERVICE_CATEGORY_CLIMATE = ["climate"]
SERVICE_CATEGORY_MEDIA = ["media_player"]