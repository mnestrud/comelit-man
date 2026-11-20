"""Constants for the Comelit Local integration."""

DOMAIN = "comelit_man"
MANUFACTURER = "Comelit"
MODEL = "6701W"

CONF_HTTP_PORT = "http_port"
CONF_ENABLE_NOTIFICATIONS = "enable_notifications"
# Opt-in: create a dedicated device user instead of reusing an existing token.
CONF_CREATE_USER = "create_dedicated_user"

DEFAULT_PORT = 64100
DEFAULT_HTTP_PORT = 8080

# User-facing labels for the entrance panel's two cameras. The panel routes
# two physical cameras (and the door strike) through a selector relay that
# advances on every new call; which camera shows which door depends on the
# relay's position, so labels are left to the user to configure.
CONF_CAMERA_LABEL_1 = "camera_1_label"
CONF_CAMERA_LABEL_2 = "camera_2_label"
DEFAULT_CAMERA_LABEL_1 = "Camera 1"
DEFAULT_CAMERA_LABEL_2 = "Camera 2"

# Increment applied to our CTPP init timestamp to derive registration-renewal
# ACK timestamps.  PCAP-verified on the 6701W (firmware 2.x).  Community notes
# for other Comelit models report the same value for the 6742W, and one source
# claims 0x01000000 for the 6701W — not what this device does.  Parameterised
# here so a differing firmware can be accommodated without hunting literals.
# Distinct from video_call.py's _CTR_INCR_* call-counter arithmetic, which
# happens to share this value but means something else.
RENEWAL_ACK_INCREMENT = 0x01010000

# Video config sent to the device via encode_video_config().
VIDEO_WIDTH = 800
VIDEO_HEIGHT = 480
VIDEO_FPS = 16
