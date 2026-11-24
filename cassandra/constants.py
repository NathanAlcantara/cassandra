"""
Static constants and configuration for Cassandra.
"""

import logging

# systemd (user) service name.
SERVICE_NAME = "cassandra.service"

# Default files (relative to the project root).
DEFAULT_DB_PATH = "monitor.db"
DEFAULT_REPORTS_DIR = "reports"
DEFAULT_CONFIG_FILE = "config.ini"

# --- Sampling intervals (seconds) ---
DEFAULT_SAMPLE_INTERVAL = 5
DEFAULT_NETWORK_INTERVAL = 60

# Consecutive samples that define the start/end of a sustained incident.
DEFAULT_SUSTAINED_SAMPLES = 6
DEFAULT_RECOVERY_SAMPLES = 6

# --- Default thresholds (used as fallback for config.ini) ---
DEFAULT_CPU_USAGE_PCT = 90.0
DEFAULT_CPU_PSI_SOME = 25.0

DEFAULT_MEM_AVAILABLE_PCT_MIN = 6.0
DEFAULT_SWAP_FREE_PCT_MIN = 10.0
DEFAULT_MEM_PSI_FULL = 5.0

DEFAULT_DISK_PSI_FULL = 20.0
DEFAULT_DISK_SPACE_USED_PCT = 90.0

DEFAULT_HTTP_TTFB_WARN_SEC = 3.0
# Consecutive bad network probes before network counts toward an incident.
DEFAULT_NETWORK_SUSTAINED_PROBES = 2
DEFAULT_HTTP_TIMEOUT_SEC = 12
DEFAULT_PUBLIC_REF = "8.8.8.8"
DEFAULT_ZTNA_PROCESS = "/opt/forticlient/ztproxy"

# Pseudo/read-only filesystems ignored in the disk check (snaps and the like
# report ~100% by nature and are not a real "disk full" condition).
SKIP_FILESYSTEMS = {
    "squashfs",
    "tmpfs",
    "devtmpfs",
    "overlay",
    "efivarfs",
    "vfat",
    "iso9660",
}

# Map of known PWA app-ids -> friendly name.
# Discover a PWA's id with: ps -eo args | grep -o 'app-id=[a-z]*'
PWA_NAMES = {
    "cifhbcnohmdccbgoicgdjpfamggdegmo": "Teams (PWA)",
}

# CPU PSI of the user's session. The system-wide /proc/pressure/cpu also counts
# stalls of CPU-capped system services (cgroup throttling of corporate agents),
# which stays high even on an idle machine.
USER_CPU_PSI_PATH = "/sys/fs/cgroup/user.slice/cpu.pressure"

# Generic timeout for network subprocesses (seconds).
SUBPROCESS_TIMEOUT = 5

# Logger shared across the whole package.
log = logging.getLogger("cassandra")
