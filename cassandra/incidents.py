"""
Incident detection: threshold evaluation, the sustained-incident state machine,
and automatic interpretation of the root cause.

An incident is only recorded when a condition is *sustained* (not isolated
spikes). When it closes, the tracker captures the top memory processes and a
plain-text diagnosis so each report is self-contained.
"""

import re
from datetime import datetime, timezone

from . import metrics


def now_iso():
    """Local ISO timestamp with timezone, seconds precision."""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def evaluate(sample, thresholds):
    """Return a list of saturation reasons for a resource sample. Empty = fine.

    `thresholds` is a Thresholds object (see config.py).
    """
    t = thresholds
    reasons = []

    # Memory (the usual culprit)
    low_ram = sample["mem_available_pct"] < t.mem_available_pct_min
    mem_psi_high = sample["psi_mem_full_avg10"] > t.mem_psi_full

    if low_ram:
        reasons.append(
            f"Available RAM {sample['mem_available_pct']:.0f}% "
            f"(< {t.mem_available_pct_min:.0f}%)")
    # Low free swap alone is a weak signal: swap stays occupied by old pages even
    # when there is plenty of RAM. Only flag it when it comes WITH real memory
    # stress — low available RAM (within 2x the threshold) or elevated memory PSI.
    # This avoids false positives where swap is low but the machine is healthy.
    swap_low = (sample["swap_total"] > 0 and
                sample["swap_free_pct"] < t.swap_free_pct_min)
    ram_getting_tight = sample["mem_available_pct"] < (t.mem_available_pct_min * 2)
    if swap_low and (ram_getting_tight or mem_psi_high):
        reasons.append(
            f"Free swap {sample['swap_free_pct']:.0f}% "
            f"(< {t.swap_free_pct_min:.0f}%) with memory under stress")
    if mem_psi_high:
        reasons.append(
            f"Memory pressure (PSI full {sample['psi_mem_full_avg10']:.1f})")

    # CPU
    if sample["cpu_usage_pct"] > t.cpu_usage_pct:
        reasons.append(f"CPU {sample['cpu_usage_pct']:.0f}%")
    if sample["psi_cpu_some_avg10"] > t.cpu_psi_some:
        reasons.append(
            f"CPU queue (PSI some {sample['psi_cpu_some_avg10']:.1f})")

    # Disk I/O
    if sample["psi_io_full_avg10"] > t.disk_psi_full:
        reasons.append(
            f"Disk saturated (PSI io full {sample['psi_io_full_avg10']:.1f})")

    # Disk space
    if sample["disk_worst_used_pct"] > t.disk_space_used_pct:
        reasons.append(
            f"Disk {sample['disk_mount']} {sample['disk_worst_used_pct']:.0f}% full")

    return reasons


def evaluate_network(net, http_ttfb_warn):
    """Return a list of network-degradation reasons.

    Slowness is judged by TTFB (server/path responsiveness), not total time:
    total includes downloading the page body, which says nothing about latency.
    """
    reasons = []
    if net.get("ztna_active") is False:
        reasons.append("ZTNA (proxy) inactive")
    for name, probe in net.get("targets", {}).items():
        if not probe["ok"]:
            reasons.append(f"{name}: connection failure ({probe.get('error')})")
        elif probe.get("ttfb") is not None and probe["ttfb"] > http_ttfb_warn:
            reasons.append(
                f"{name}: slow response TTFB {probe['ttfb']:.1f}s "
                f"(> {http_ttfb_warn:g}s)")
    return reasons


def categorize(reasons, net_reasons):
    """Derive an incident category string from the reasons."""
    cats = []
    if any("RAM" in r or "swap" in r.lower() or "memory" in r.lower()
           for r in reasons):
        cats.append("memory")
    if any("CPU" in r for r in reasons):
        cats.append("cpu")
    if any("Disk" in r for r in reasons):
        cats.append("disk")
    if net_reasons:
        cats.append("network")
    return "+".join(cats) if cats else "general"


def summarize_reasons(reasons):
    """Group repeated reasons (same condition, different values) into one line
    each, with a count and value range. Avoids listing dozens of near-identical
    readings. Returns a list of markdown bullet strings.
    """
    groups = {}
    for r in reasons:
        key = re.sub(r"[-\d]+\.?\d*", "", r).strip()
        key = re.sub(r"\(\s*[%<>]*\s*\)", "", key)
        key = re.sub(r"\(\s*PSI[^)]*\)", "(PSI)", key)
        key = key.replace("% (< %)", "").replace("%", "")
        key = re.sub(r"\s{2,}", " ", key).strip(" :=(")
        nums = [float(x) for x in re.findall(r"\d+\.?\d*", r)]
        val = nums[0] if nums else None
        g = groups.setdefault(key, {"count": 0, "min": val, "max": val, "raw": r})
        g["count"] += 1
        if val is not None:
            g["min"] = val if g["min"] is None else min(g["min"], val)
            g["max"] = val if g["max"] is None else max(g["max"], val)
    out = []
    for key, g in sorted(groups.items(), key=lambda x: -x[1]["count"]):
        if g["count"] == 1:
            out.append(f"- {g['raw']}")
        elif g["min"] is not None and g["min"] != g["max"]:
            out.append(f"- {key} — {g['count']}x, ranging from "
                       f"{g['min']:g} to {g['max']:g}")
        else:
            out.append(f"- {key} — {g['count']}x")
    return out


def interpret(incident):
    """Produce a textual root-cause diagnosis from the incident data, so the
    report does not depend on external context to be understood.
    """
    p = incident["peak"]
    notes = []
    mem_pressure = (p["mem_available_pct"] < 10 or p["swap_free_pct"] < 10 or
                    p["psi_mem_full_avg10"] > 5)
    io_pressure = p["psi_io_full_avg10"] > 20

    if mem_pressure and io_pressure:
        notes.append(
            "**Likely root cause: RAM exhaustion.** High memory pressure (PSI "
            "memory) with swap nearly exhausted indicates *thrashing* — the "
            "kernel starts moving pages between RAM and disk. The saturated disk "
            "(high PSI io) here is a **symptom** of that, NOT a slow or full "
            "disk. The fix is to free/add memory, not touch the disk.")
    elif mem_pressure:
        notes.append(
            "**Likely root cause: memory pressure.** Low available RAM and/or "
            "exhausted swap. Reducing RAM usage (closing heavy apps) or adding "
            "physical memory resolves it.")
    elif io_pressure:
        notes.append(
            "**Likely root cause: disk I/O.** Disk saturation with memory to "
            "spare — investigate processes doing heavy read/write.")
    demand, total = p.get("mem_demand_gb"), p.get("mem_total_gb")
    if demand and total and demand > total:
        notes.append(
            f"**Memory demand {demand:.1f} GB exceeded the {total:.1f} GB of "
            f"physical RAM** (RAM in use + {p.get('zram_orig_gb', 0):.1f} GB "
            f"compressed into zram). The workload does not fit in this machine's "
            f"RAM; zram only hides it at the cost of CPU for compression.")
    if p["psi_cpu_some_avg10"] > 25 or p["cpu_usage_pct"] > 90:
        notes.append("There was also a **CPU queue** (processes waiting to run).")

    net = incident.get("net") or {}
    net_fail = any(not t.get("ok") for t in net.get("targets", {}).values())
    if net_fail:
        rtt = net.get("public_ref_rtt_ms")
        if mem_pressure:
            notes.append(
                "The failures reaching company services occurred while the "
                "machine was memory-starved, so they are a **likely consequence** "
                "of the local freeze, not necessarily the infrastructure.")
        elif rtt is not None and rtt < 50:
            notes.append(
                f"The failures reaching company services happened with good local "
                f"internet (public RTT {rtt:.0f} ms) and no resource pressure — "
                f"this points to the **ZTNA path / company infrastructure** as the "
                f"bottleneck, not the local machine.")
    return notes


# Peak = worst value of each metric during the incident (not one sample), so a
# CPU incident shows its real CPU peak and a memory one its real RAM low.
_PEAK_MIN = ("mem_available_pct", "mem_available_gb", "swap_free_pct")
_PEAK_MAX = ("cpu_usage_pct", "psi_cpu_some_avg10", "psi_mem_full_avg10",
             "psi_io_full_avg10", "disk_worst_used_pct", "load1", "swap_used_gb",
             "zram_orig_gb", "zram_ram_gb", "mem_demand_gb")


def _merge_peak(peak, sample):
    for k in _PEAK_MIN:
        if k in sample:
            peak[k] = min(peak.get(k, sample[k]), sample[k])
    for k in _PEAK_MAX:
        if k in sample:
            peak[k] = max(peak.get(k, sample[k]), sample[k])


class IncidentTracker:
    """State machine that detects the start/end of a sustained incident."""

    def __init__(self, sustained_samples, recovery_samples):
        self.sustained = sustained_samples
        self.recovery = recovery_samples
        self.bad_streak = 0
        self.good_streak = 0
        self.streak_start = None
        self.active = None  # data of the ongoing incident

    def update(self, sample, reasons, net_reasons, net_snapshot):
        """Feed one evaluated sample. Returns a closed incident dict, or None."""
        if reasons or net_reasons:
            self.bad_streak += 1
            self.good_streak = 0
            if self.bad_streak == 1:
                self.streak_start = now_iso()
            if self.active is None and self.bad_streak >= self.sustained:
                # Start = first bad sample of the streak, not when it got confirmed.
                self.active = {"start_ts": self.streak_start, "local": set(),
                               "netr": set(), "peak": dict(sample), "net": None}
            if self.active is not None:
                a = self.active
                a["local"].update(reasons)
                a["netr"].update(net_reasons)
                # Keep the snapshot that shows the failure (latest failing one).
                if net_reasons or a["net"] is None:
                    a["net"] = net_snapshot
                # Snapshot "who" at the worst moment, not after recovery (by then
                # the culprits may have shrunk or exited).
                if ("top_procs" not in a or
                        sample["mem_available_pct"] < a["peak"]["mem_available_pct"]):
                    a["top_procs"] = metrics.top_mem_processes(5)
                cpu_now = max(sample["cpu_usage_pct"], sample["psi_cpu_some_avg10"])
                cpu_peak = max(a["peak"]["cpu_usage_pct"],
                               a["peak"]["psi_cpu_some_avg10"])
                if "top_cpu_procs" not in a or cpu_now > cpu_peak:
                    a["top_cpu_procs"] = metrics.top_cpu_processes(5)
                _merge_peak(a["peak"], sample)
        else:
            self.good_streak += 1
            self.bad_streak = 0
            if self.active is not None and self.good_streak >= self.recovery:
                return self._close()
        return None

    def interrupt(self, end_ts):
        """Monitoring stopped (suspend/shutdown): close any open incident at the
        last observed sample and reset the streaks. Returns it, or None."""
        self.bad_streak = self.good_streak = 0
        if self.active is None:
            return None
        return self._close(end_ts)

    def _close(self, end_ts=None):
        inc = self.active
        self.active = None
        local, netr = inc.pop("local"), inc.pop("netr")
        # Category from everything seen, not only the opening sample.
        inc["category"] = categorize(sorted(local), sorted(netr))
        inc["reasons"] = sorted(local | netr)
        inc["end_ts"] = end_ts or now_iso()
        start = datetime.fromisoformat(inc["start_ts"])
        end = datetime.fromisoformat(inc["end_ts"])
        inc["duration_sec"] = int((end - start).total_seconds())
        inc["chrome_breakdown"] = (
            metrics.chrome_breakdown() if "memory" in inc["category"] else {})
        return inc
