"""
The monitoring daemon: the sampling loop that ties everything together.

Leanness philosophy:
  - Reads /proc directly; during a bottleneck it only appends a few bytes to
    SQLite. Should run with `Nice=19` + idle CPU/IO (the systemd unit does this).
  - Notifies only AFTER the machine recovers (never during the freeze), so the
    alert does not compete for resources at the worst moment.
  - PSI is the primary trigger: the kernel records stall time continuously, so
    the bottleneck is captured even if the monitor sampled with a delay.
"""

import os
import signal
import threading
import time

from . import metrics, network, notifier, report, storage
from .constants import log
from .incidents import IncidentTracker, evaluate, evaluate_network, now_iso


# A wall-clock gap this large between samples means the machine was suspended
# or the monitor was stopped: nothing was observed, so no incident spans it.
GAP_SEC = 60


def _network_worker(config, latest, stop):
    """Probe the network in the background so slow probes (curl timeouts can
    take 15s+) never stall the resource sampling loop."""
    while not stop.is_set():
        snap = network.collect(
            config.network.targets, config.network.public_ref,
            config.network.ztna_process, config.network.http_timeout)
        latest["snap"], latest["seq"] = snap, latest["seq"] + 1
        stop.wait(config.network_interval)


def _record(conn, config, closed):
    """Persist a closed incident, write its report and notify."""
    inc_id = storage.insert_incident(conn, closed)
    report.write_incident_md(closed, inc_id, config.reports_dir)
    log.info("Incident #%d recorded (%s, %ds).",
             inc_id, closed["category"], closed["duration_sec"])
    # Network-only incidents are company infrastructure, nothing to act on
    # locally: recorded in the reports, but no desktop notification.
    if config.notify_after_incident and closed["category"] != "network":
        md_path = os.path.join(config.reports_dir, "incidents",
                               f"incident_{inc_id}.md")
        notifier.notify_with_open(
            f"Bottleneck recorded (#{inc_id}) — {closed['category']}",
            f"Duration {closed['duration_sec']}s. Click to open the report.",
            md_path, config.open_command, config.notification_timeout_ms)


def run(config):
    """Run the monitoring loop until SIGTERM/SIGINT. Blocks."""
    conn = storage.init_db(config.db_path)
    tracker = IncidentTracker(config.sustained_samples, config.recovery_samples)

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    latest = {"snap": None, "seq": 0}
    threading.Thread(target=_network_worker, args=(config, latest, stop),
                     daemon=True, name="network").start()

    prev = metrics.initial_prev()
    seen_seq = 0
    net_snapshot = None
    probe_reasons = []
    net_bad_probes = 0
    pwa_last_alert = 0.0
    last_wall, last_ts = None, None

    log.info("Cassandra started; watching every %ds (network every %ds).",
             config.sample_interval, config.network_interval)

    while not stop.is_set():
        loop_start = time.monotonic()
        wall = time.time()
        if last_wall is not None and wall - last_wall > GAP_SEC:
            log.info("Gap of %ds (suspend/stop); closing any open incident.",
                     wall - last_wall)
            closed = tracker.interrupt(last_ts)
            if closed is not None:
                _record(conn, config, closed)
            prev = metrics.initial_prev()  # deltas across the gap are meaningless

        sample, prev = metrics.collect_sample(prev)

        # Consume a new network result, if the worker produced one.
        if latest["seq"] != seen_seq:
            seen_seq, net_snapshot = latest["seq"], latest["snap"]
            probe_reasons = evaluate_network(net_snapshot, config.network.http_ttfb_warn)
            net_bad_probes = net_bad_probes + 1 if probe_reasons else 0

        reasons = evaluate(sample, config.thresholds)
        # A probe result is reused by every sample until the next probe, so one
        # blip would look "sustained": on its own, network needs consecutive bad
        # probes. During a local bottleneck any failure is kept as evidence (it is
        # likely a consequence of the machine being saturated).
        confirmed = net_bad_probes >= config.network.sustained_probes
        net_reasons = probe_reasons if (confirmed or reasons) else []
        # 1 = local resource bottleneck, 2 = network-only degradation, 0 = fine.
        in_incident = 1 if reasons else (2 if net_reasons else 0)
        last_ts = now_iso()
        storage.insert_sample(conn, last_ts, sample, in_incident)

        closed = tracker.update(sample, reasons, net_reasons, net_snapshot)
        if closed is not None:
            _record(conn, config, closed)

        # PWA watch (e.g. Teams): warn to reload when over the cap, but only in a
        # SAFE moment (no active incident) and respecting the cooldown.
        pw = config.pwa_watch
        if pw.enabled and pw.app_id and tracker.active is None:
            total, _, age = metrics.pwa_tree_rss(pw.app_id)
            if (total >= pw.threshold_bytes and
                    loop_start - pwa_last_alert >= pw.cooldown_sec):
                gb = total / 1024**3
                hours = age // 3600
                log.info("%s at %.1f GB (open ~%dh).", pw.label, gb, hours)
                if config.notify_after_incident:
                    notifier.notify(
                        f"{pw.label} at {gb:.1f} GB — consider reloading",
                        f"Open for ~{hours}h. Reload with Ctrl+R inside {pw.label} "
                        f"to free memory without closing the app.")
                pwa_last_alert = loop_start

        last_wall = time.time()
        stop.wait(max(0, config.sample_interval - (time.monotonic() - loop_start)))

    conn.close()
    log.info("Cassandra stopped.")
