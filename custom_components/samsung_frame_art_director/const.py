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
CONF_OPENAI_API_KEY = "openai_api_key"
CONF_AI_PROVIDER = "ai_provider"
CONF_AI_MODEL = "ai_model"
CONF_MATTE_ENABLED = "matte_enabled"

# AI vision providers used for auto-tagging
AI_PROVIDER_GEMINI = "gemini"
AI_PROVIDER_OPENAI = "openai"

# Default models per provider. gemini-2.0-flash is being shut down (2026-06-01),
# so default to a current model; users can override via the AI Model option.
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_OPENAI_MODEL = "gpt-4o"

SLIDESHOW_SOURCE_FOLDER = "folder"
SLIDESHOW_SOURCE_TAGS = "Tags"
SLIDESHOW_SOURCE_LIBRARY = "All Library"

# Matte (the digital passe-partout border the Frame draws around art).
# A matte id is "{style}_{color}" (e.g. "shadowbox_polar"), or "none".
# Values vary by model/firmware; these are the documented supersets.
CONF_MATTE_STYLE = "matte_style"
CONF_MATTE_COLOR = "matte_color"

MATTE_STYLE_NONE = "none"
MATTE_STYLES = [
    MATTE_STYLE_NONE,
    "modernthin",
    "modern",
    "modernwide",
    "flexible",
    "shadowbox",
    "panoramic",
    "triptych",
    "mix",
    "squares",
]
MATTE_COLORS = [
    "black",
    "neutral",
    "antique",
    "warm",
    "polar",
    "sand",
    "seafoam",
    "sage",
    "burgandy",
    "navy",
    "apricot",
    "byzantine",
    "lavender",
    "redorange",
    "skyblue",
    "turquoise",
]
DEFAULT_MATTE_STYLE = "shadowbox"
DEFAULT_MATTE_COLOR = "polar"


def resolve_matte(options) -> str:
    """Resolve the configured matte id from entry options.

    Returns a matte id like ``"shadowbox_polar"`` or ``"none"``. Mattes are
    ``"{style}_{color}"``. Installs configured before the style/color pickers
    existed only had the on/off ``matte_enabled`` switch, so fall back to a
    sensible default matte when that legacy flag is set.
    """
    style = options.get(CONF_MATTE_STYLE)
    if style is None:
        # Legacy: only the boolean matte_enabled switch existed.
        if options.get(CONF_MATTE_ENABLED):
            return f"{DEFAULT_MATTE_STYLE}_{DEFAULT_MATTE_COLOR}"
        return "none"
    if not style or style == MATTE_STYLE_NONE:
        return "none"
    color = options.get(CONF_MATTE_COLOR) or DEFAULT_MATTE_COLOR
    return f"{style}_{color}"

