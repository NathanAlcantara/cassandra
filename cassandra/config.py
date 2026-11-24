"""
Configuration: reads config.ini and exposes typed objects for the rest of the
package. Falls back to the defaults in constants.py when a key is absent.
"""

import configparser
import os

from . import constants as C


class Thresholds:
    """Resource thresholds that define a bottleneck."""

    def __init__(
        self,
        cpu_usage_pct,
        cpu_psi_some,
        mem_available_pct_min,
        swap_free_pct_min,
        mem_psi_full,
        disk_psi_full,
        disk_space_used_pct,
    ):
        self.cpu_usage_pct = cpu_usage_pct
        self.cpu_psi_some = cpu_psi_some
        self.mem_available_pct_min = mem_available_pct_min
        self.swap_free_pct_min = swap_free_pct_min
        self.mem_psi_full = mem_psi_full
        self.disk_psi_full = disk_psi_full
        self.disk_space_used_pct = disk_space_used_pct


class Network:
    """Network monitoring settings."""

    def __init__(self, targets, public_ref, http_ttfb_warn, http_timeout, ztna_process,
                 sustained_probes):
        self.targets = targets              # list of (name, url)
        self.public_ref = public_ref
        self.http_ttfb_warn = http_ttfb_warn
        self.sustained_probes = sustained_probes
        self.http_timeout = http_timeout
        self.ztna_process = ztna_process


class PwaWatch:
    """Optional watcher for a memory-leaking PWA (e.g. Teams)."""

    def __init__(self, enabled, app_id, label, threshold_bytes, cooldown_sec):
        self.enabled = enabled
        self.app_id = app_id
        self.label = label
        self.threshold_bytes = threshold_bytes
        self.cooldown_sec = cooldown_sec


class Config:
    """Full resolved configuration."""

    def __init__(self, base_dir, sample_interval, network_interval,
                 sustained_samples, recovery_samples, db_path, reports_dir,
                 notify_after_incident, open_command, notification_timeout_ms,
                 thresholds, network, pwa_watch):
        self.base_dir = base_dir
        self.sample_interval = sample_interval
        self.network_interval = network_interval
        self.sustained_samples = sustained_samples
        self.recovery_samples = recovery_samples
        self.db_path = db_path
        self.reports_dir = reports_dir
        self.notify_after_incident = notify_after_incident
        self.open_command = open_command
        self.notification_timeout_ms = notification_timeout_ms
        self.thresholds = thresholds
        self.network = network
        self.pwa_watch = pwa_watch


def _parse_targets(raw):
    """Parse 'name|url , name|url' into a list of (name, url) tuples."""
    targets = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry or "|" not in entry:
            continue
        name, url = (x.strip() for x in entry.split("|", 1))
        targets.append((name, url))
    return targets


def build_config(base_dir, config_file=None):
    """Read config.ini from base_dir and build a Config object."""
    cfg = configparser.ConfigParser()
    path = os.path.join(base_dir, config_file or C.DEFAULT_CONFIG_FILE)
    cfg.read(path)

    def getf(section, key, fallback):
        return cfg.getfloat(section, key, fallback=fallback) \
            if cfg.has_section(section) else fallback

    def geti(section, key, fallback):
        return cfg.getint(section, key, fallback=fallback) \
            if cfg.has_section(section) else fallback

    def gets(section, key, fallback):
        return cfg.get(section, key, fallback=fallback) \
            if cfg.has_section(section) else fallback

    def getb(section, key, fallback):
        return cfg.getboolean(section, key, fallback=fallback) \
            if cfg.has_section(section) else fallback

    thresholds = Thresholds(
        cpu_usage_pct=getf("cpu", "usage_pct", C.DEFAULT_CPU_USAGE_PCT),
        cpu_psi_some=getf("cpu", "psi_some_avg10", C.DEFAULT_CPU_PSI_SOME),
        mem_available_pct_min=getf("memory", "available_pct_min",
                                   C.DEFAULT_MEM_AVAILABLE_PCT_MIN),
        swap_free_pct_min=getf("memory", "swap_free_pct_min",
                               C.DEFAULT_SWAP_FREE_PCT_MIN),
        mem_psi_full=getf("memory", "psi_full_avg10", C.DEFAULT_MEM_PSI_FULL),
        disk_psi_full=getf("disk", "psi_full_avg10", C.DEFAULT_DISK_PSI_FULL),
        disk_space_used_pct=getf("disk", "space_used_pct",
                                 C.DEFAULT_DISK_SPACE_USED_PCT),
    )

    network = Network(
        targets=_parse_targets(gets("network", "targets", "")),
        public_ref=gets("network", "public_ref", C.DEFAULT_PUBLIC_REF),
        http_ttfb_warn=getf("network", "http_ttfb_warn_sec",
                            C.DEFAULT_HTTP_TTFB_WARN_SEC),
        sustained_probes=geti("network", "sustained_probes",
                              C.DEFAULT_NETWORK_SUSTAINED_PROBES),
        http_timeout=geti("network", "http_timeout_sec",
                          C.DEFAULT_HTTP_TIMEOUT_SEC),
        ztna_process=gets("network", "ztna_process", C.DEFAULT_ZTNA_PROCESS),
    )

    pwa_watch = PwaWatch(
        enabled=getb("pwa_watch", "enabled", False),
        app_id=gets("pwa_watch", "app_id", ""),
        label=gets("pwa_watch", "label", "PWA"),
        threshold_bytes=int(getf("pwa_watch", "threshold_mb", 1200) * 1024 * 1024),
        cooldown_sec=int(getf("pwa_watch", "cooldown_min", 60) * 60),
    )

    return Config(
        base_dir=base_dir,
        sample_interval=geti("general", "sample_interval_sec",
                             C.DEFAULT_SAMPLE_INTERVAL),
        network_interval=geti("general", "network_interval_sec",
                              C.DEFAULT_NETWORK_INTERVAL),
        sustained_samples=geti("general", "sustained_samples",
                               C.DEFAULT_SUSTAINED_SAMPLES),
        recovery_samples=geti("general", "recovery_samples",
                              C.DEFAULT_RECOVERY_SAMPLES),
        db_path=os.path.join(base_dir, gets("general", "db_path",
                                            C.DEFAULT_DB_PATH)),
        reports_dir=os.path.join(base_dir, gets("general", "reports_dir",
                                                C.DEFAULT_REPORTS_DIR)),
        notify_after_incident=getb("general", "notify_after_incident", True),
        open_command=gets("general", "open_command", "xdg-open"),
        notification_timeout_ms=geti("general", "notification_timeout_ms", 20000),
        thresholds=thresholds,
        network=network,
        pwa_watch=pwa_watch,
    )
