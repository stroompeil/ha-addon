"""Constants for the Stroompeil HA addon integration."""

DOMAIN = "stroompeil_ha_addon"

CONF_ENVIRONMENT = "environment"
CONF_SERVER_URL = "server_url"
CONF_TOKEN = "token"
CONF_HOST_NAME = "host_name"

ENVIRONMENT_TEST = "test"
ENVIRONMENT_PRODUCTION = "production"

ENVIRONMENT_URLS = {
    ENVIRONMENT_TEST: "wss://fleet-dev.stroompeil.nl",
    ENVIRONMENT_PRODUCTION: "",
}

DEFAULT_STATUS_INTERVAL_SECONDS = 60
DEFAULT_BACKOFF_INITIAL_SECONDS = 2
DEFAULT_BACKOFF_MAX_SECONDS = 300

COMMAND_TYPE_RESTART = "homeassistant.restart"
COMMAND_TYPE_UPDATE = "integration.update"
COMMAND_TYPE_LOGS = "diagnostics.logs"
