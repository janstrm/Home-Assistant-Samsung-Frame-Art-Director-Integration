"""Constants for Samsung Frame Art Director integration."""

DOMAIN = "samsung_frame_art_director"

# Integration-specific keys
CONF_DUID = "duid"
DATA_CLIENT = "client"

# Pairing / methods / results aligned with official approach
METHOD_WEBSOCKET = "websocket"
METHOD_ENCRYPTED = "encrypted"

WEBSOCKET_SSL_PORT = 8002
WEBSOCKET_NO_SSL_PORT = 8001
WEBSOCKET_PORTS = (WEBSOCKET_SSL_PORT, WEBSOCKET_NO_SSL_PORT)
ENCRYPTED_WEBSOCKET_PORT = 8000

# Timeouts aligned with official integration semantics
TIMEOUT_REQUEST = 31
TIMEOUT_WEBSOCKET = 5

RESULT_AUTH_MISSING = "auth_missing"
RESULT_SUCCESS = "success"
RESULT_CANNOT_CONNECT = "cannot_connect"
RESULT_NOT_SUPPORTED = "not_supported"
RESULT_INVALID_PIN = "invalid_pin"

# Defaults / misc
CLIENT_NAME = "Home Assistant Art Director"
PAIRING_TOKENS_DIR = "pairing_tokens"

# Optional fields when using encrypted pairing
CONF_SESSION_ID = "session_id"

# Director/DB paths
DB_DIR = "samsung_frame_director"
DB_FILE = "art_library.db"

# Cleanup defaults
DEFAULT_CLEANUP_MAX_ITEMS = 50
DEFAULT_CLEANUP_PRESERVE_CURRENT = True
DEFAULT_CLEANUP_ONLY_INTEGRATION_MANAGED = True
DEFAULT_CLEANUP_DRY_RUN = False

# Slideshow constants
CONF_SLIDESHOW_INTERVAL = "slideshow_interval"
CONF_SLIDESHOW_SOURCE_PATH = "slideshow_source_dir"  # Legacy, kept for folder path default
CONF_SLIDESHOW_ENABLED = "slideshow_enabled"
CONF_SLIDESHOW_SOURCE_TYPE = "slideshow_source_type"
CONF_SLIDESHOW_FILTER = "slideshow_filter"
CONF_SLIDESHOW_INTERVAL = "slideshow_interval"
CONF_GEMINI_API_KEY = "gemini_api_key"
CONF_MATTE_ENABLED = "matte_enabled"

SLIDESHOW_SOURCE_FOLDER = "folder"
SLIDESHOW_SOURCE_TAGS = "Tags"
SLIDESHOW_SOURCE_LIBRARY = "All Library"
