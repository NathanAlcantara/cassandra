"""
Report generation: per-incident markdown, the monthly consolidated report,
and the CONTEXT.md briefing that ships alongside them.
"""

import json
import os
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime

from . import metrics
from .incidents import interpret, summarize_reasons

# Project root (one level above this package).
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fmt_duration(sec):
    """Human-readable duration."""
    if sec < 60:
        return f"{sec}s"
    m, s = divmod(sec, 60)
    if m < 60:
        return f"{m}min {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}min"


# --------------------------------------------------------------------------- #
# Per-incident markdown
# --------------------------------------------------------------------------- #
def write_incident_md(inc, inc_id, reports_dir):
    """Write a self-contained markdown report for a single incident."""
    d = os.path.join(reports_dir, "incidents")
    os.makedirs(d, exist_ok=True)
    p = inc["peak"]
    mi = metrics.machine_info()

    lines = [
        f"# Incident #{inc_id} — {inc['category']}",
        "",
        "> For an accurate analysis of this incident, read it together with the "
        "`CONTEXT.md` file (project root), which explains the environment, the "
        "hardware, the meaning of the PSI metrics and the process glossary.",
        "",
        "## Environment",
        f"- **Machine:** {mi['cpu']} · {mi['cores']} threads · {mi['ram_gb']} GB RAM",
        f"- **OS:** {mi['distro']}",
        "",
        f"- **Start:** {inc['start_ts']}",
        f"- **End:** {inc['end_ts']}",
        f"- **Duration:** {inc['duration_sec']}s",
        "",
        "## Diagnosis (automatic interpretation)",
    ]
    notes = interpret(inc)
    lines += [f"- {n}" for n in notes] if notes else \
        ["- Sustained condition detected; see metrics below."]

    lines += ["", "## Reasons detected"]
    lines += summarize_reasons(inc["reasons"])

    lines += [
        "",
        "## Resource snapshot at the peak",
        "",
        "### CPU",
        f"- Usage: {p['cpu_usage_pct']:.0f}% | PSI cpu(some): {p['psi_cpu_some_avg10']:.1f}",
        f"- Load average: {p.get('load1', 0):.2f} (1m) / "
        f"{p.get('load5', 0):.2f} (5m) / {p.get('load15', 0):.2f} (15m)",
        "",
        "### Memory",
        f"- Available RAM: {p['mem_available_pct']:.0f}% "
        f"({p.get('mem_available_gb', '?')} of {p.get('mem_total_gb', mi['ram_gb'])} GB)",
        f"- Free swap: {p['swap_free_pct']:.0f}% "
        f"(used {p.get('swap_used_gb', '?')} GB)",
        f"- PSI memory(full): {p['psi_mem_full_avg10']:.1f}  "
        f"(time tasks were stalled waiting for RAM)",
        f"- zram (compressed swap in RAM): {p.get('zram_orig_gb', 0)} GB of data "
        f"held in {p.get('zram_ram_gb', 0)} GB of RAM",
        f"- **Memory demand: {p.get('mem_demand_gb', '?')} GB** vs "
        f"{p.get('mem_total_gb', mi['ram_gb'])} GB physical "
        f"(RAM in use + data compressed into zram)",
        "",
        "### Disk",
        f"- Space: {p['disk_mount']} {p['disk_worst_used_pct']:.0f}% used",
        f"- I/O throughput: {p.get('disk_read_mbs', 0):.1f} MB/s read, "
        f"{p.get('disk_write_mbs', 0):.1f} MB/s write",
        f"- PSI io(full): {p['psi_io_full_avg10']:.1f}  "
        f"(high together with memory pressure = thrashing, not a slow disk)",
        "",
        "### Network",
        f"- Throughput: {p.get('net_rx_mbs', 0):.1f} MB/s in, "
        f"{p.get('net_tx_mbs', 0):.1f} MB/s out",
        "",
        "## Processes using the most RAM",
    ]
    for proc in inc["top_procs"]:
        lines.append(f"- {proc['name']} (pid {proc['pid']}): {proc['rss_mb']} MB")

    cpu_procs = inc.get("top_cpu_procs") or []
    if cpu_procs:
        lines += ["", "## Processes using the most CPU"]
        for proc in cpu_procs:
            lines.append(f"- {proc['name']} (pid {proc['pid']}): {proc['cpu_pct']}%")

    cb = inc.get("chrome_breakdown") or {}
    if cb:
        lines += ["", "## Chrome — breakdown by usage"]
        for cat, mb in cb.items():
            lines.append(f"- {cat}: {mb} MB")

    net = inc.get("net")
    if net:
        lines += ["", "## Network state (ZTNA)"]
        lines.append(f"- ZTNA active: {net.get('ztna_active')}")
        lines.append(f"- Public RTT (ref): {net.get('public_ref_rtt_ms')} ms")
        for name, t in net.get("targets", {}).items():
            if t.get("ok"):
                lines.append(
                    f"- {name}: total {t['total']:.2f}s, TTFB {t['ttfb']:.2f}s, "
                    f"HTTP {t['http_code']}, IP {t.get('resolved_ip')} "
                    f"(via ZTNA DNS: {t.get('via_ztna_dns')})")
            else:
                lines.append(f"- {name}: FAILURE ({t.get('error')})")

    with open(os.path.join(d, f"incident_{inc_id}.md"), "w") as f:
        f.write("\n".join(lines) + "\n")


# --------------------------------------------------------------------------- #
# CONTEXT.md
# --------------------------------------------------------------------------- #
def write_context_md(reports_dir):
    """Copy the curated CONTEXT.md (project root) into reports/ so it ships
    alongside the reports."""
    src = os.path.join(PROJECT_ROOT, "CONTEXT.md")
    if os.path.exists(src):
        with open(src) as f:
            content = f.read()
    else:
        content = ("# CONTEXT.md\n\n(base file missing: copy CONTEXT.md.example "
                   "to CONTEXT.md and describe your environment)\n")
    os.makedirs(reports_dir, exist_ok=True)
    dst = os.path.join(reports_dir, "CONTEXT.md")
    with open(dst, "w") as f:
        f.write(content)
    return dst


# --------------------------------------------------------------------------- #
# Monthly report
# --------------------------------------------------------------------------- #
def generate(db_path, reports_dir, month):
    """Generate the monthly consolidated report. Returns the output path."""
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database {db_path} not found. Has the monitor run?")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    incidents = conn.execute(
        "SELECT * FROM incidents WHERE start_ts LIKE ? ORDER BY start_ts",
        (f"{month}%",)).fetchall()
    total_samples = conn.execute(
        "SELECT COUNT(*) FROM samples WHERE ts LIKE ?", (f"{month}%",)
    ).fetchone()[0]
    bad_samples = conn.execute(
        "SELECT COUNT(*) FROM samples WHERE ts LIKE ? AND in_incident=1",
        (f"{month}%",)).fetchone()[0]

    write_context_md(reports_dir)

    lines = [
        f"# Machine performance report — {month}",
        "",
        f"Generated at: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "> **How to use this report with an AI or with the IT team:** send this "
        "file TOGETHER with `CONTEXT.md` (same folder) and the individual reports "
        "in `incidents/`. `CONTEXT.md` holds the environment briefing (hardware, "
        "PSI metrics, process glossary, ZTNA network) needed for an accurate "
        "analysis. Without it, the analysis is generic.",
        "",
        "## Executive summary",
        "",
    ]

    if not incidents:
        lines.append(f"No bottlenecks recorded in {month}. "
                     f"{total_samples} samples collected.")
        return _write(reports_dir, month, lines)

    # Machine bottlenecks (local CPU/RAM/disk) are the upgrade evidence; incidents
    # that are network-only point to the company infrastructure, not the device.
    local = [i for i in incidents if i["category"] != "network"]
    net_only = len(incidents) - len(local)
    cat_counter = Counter(i["category"] for i in incidents)
    pct_bad = (bad_samples / total_samples * 100) if total_samples else 0

    lines += [
        f"- **Machine bottlenecks (CPU/RAM/disk):** {len(local)}",
        f"- **Time with the machine bottlenecked:** "
        f"{fmt_duration(sum(i['duration_sec'] for i in local))} "
        f"({pct_bad:.1f}% of monitored samples)",
    ]
    if local:
        longest = max(local, key=lambda i: i["duration_sec"])
        lines.append(f"- **Longest machine bottleneck:** "
                     f"{fmt_duration(longest['duration_sec'])} "
                     f"at {longest['start_ts']} ({longest['category']})")
    lines += [
        f"- **Network-only incidents (company infra/ZTNA, not the machine):** "
        f"{net_only}",
        "",
        "### Incidents by category",
        "",
        "| Category | Occurrences |",
        "|---|---|",
    ]
    for cat, n in cat_counter.most_common():
        lines.append(f"| {cat} | {n} |")
    lines.append("")

    # Distribution by hour of day
    by_hour = defaultdict(int)
    for i in incidents:
        try:
            by_hour[datetime.fromisoformat(i["start_ts"]).hour] += 1
        except ValueError:
            pass
    if by_hour:
        lines += ["### Distribution by hour of day", "",
                  "| Hour | Incidents |", "|---|---|"]
        for h in sorted(by_hour):
            lines.append(f"| {h:02d}:00 | {by_hour[h]} |")
        lines.append("")

    # Memory analysis
    mem_incidents = [i for i in incidents if "memory" in i["category"]]
    if mem_incidents:
        lines += ["### Memory analysis (RAM/Swap)", ""]
        proc_counter = Counter()
        min_ram = 100.0
        for i in mem_incidents:
            peak = json.loads(i["peak_json"])
            min_ram = min(min_ram, peak.get("mem_available_pct", 100))
            for p in json.loads(i["top_procs_json"]):
                proc_counter[p["name"]] += 1
        lines.append(f"- Incidents involving memory: **{len(mem_incidents)}**")
        lines.append(f"- Lowest available RAM recorded: **{min_ram:.0f}%**")
        if proc_counter:
            lines.append("- Most recurrent processes during RAM peaks:")
            for name, n in proc_counter.most_common(5):
                lines.append(f"  - {name} (in {n} incidents)")
        lines.append("")

    # Memory demand vs physical RAM: the core upgrade evidence (samples from
    # before this metric existed have NULL and are ignored).
    demand = [r[0] for r in conn.execute(
        "SELECT mem_demand_gb FROM samples WHERE ts LIKE ? "
        "AND mem_demand_gb IS NOT NULL ORDER BY mem_demand_gb", (f"{month}%",))]
    if demand:
        ram_total = metrics.read_meminfo()["mem_total"] / 1024**3
        over = sum(1 for d in demand if d > ram_total) / len(demand) * 100
        lines += [
            "### Memory demand vs physical RAM", "",
            f"- Physical RAM: **{ram_total:.1f} GB**",
            f"- Memory demand (RAM in use + data compressed into zram): "
            f"median **{demand[len(demand) // 2]:.1f} GB**, "
            f"p95 **{demand[int(len(demand) * 0.95)]:.1f} GB**, "
            f"peak **{demand[-1]:.1f} GB**",
            f"- Time with demand above physical RAM: **{over:.1f}%** of samples",
            "",
            "> Demand above physical RAM means the workload does not fit in this "
            "machine's memory. zram (compressed swap in RAM) keeps it running by "
            "spending CPU on compression; it hides the shortage, it does not fix it.",
            ""]

    # Network analysis
    net_incidents = [i for i in incidents if "network" in i["category"]]
    if net_incidents:
        lines += ["### Network analysis (ZTNA / access to company services)", "",
                  f"- Incidents involving network: **{len(net_incidents)}**"]
        for i in net_incidents:
            net = json.loads(i["network_json"]) if i["network_json"] else {}
            rtt = net.get("public_ref_rtt_ms")
            note = ""
            if rtt is not None and rtt < 50:
                note = " — local internet OK (low public RTT): points to ZTNA/infra"
            lines.append(f"  - {i['start_ts']}: {i['reasons']}{note}")
        lines += ["",
                  "> Note: when latency to the public target is low and the "
                  "machine is idle, degraded access to company services points to "
                  "the ZTNA/company-infrastructure path, not the local device or "
                  "the home internet.", ""]

    # Full detail
    lines += ["## Incident detail", "",
              "| # | Start | Duration | Category | Reasons |", "|---|---|---|---|---|"]
    for i in incidents:
        reasons = ", ".join(json.loads(i["reasons"]))
        lines.append(f"| {i['id']} | {i['start_ts']} | "
                     f"{fmt_duration(i['duration_sec'])} | {i['category']} | {reasons} |")
    lines += ["", "Individual reports for each incident are in "
              "`reports/incidents/incident_<N>.md`."]

    conn.close()
    return _write(reports_dir, month, lines)


def _write(reports_dir, month, lines):
    os.makedirs(reports_dir, exist_ok=True)
    path = os.path.join(reports_dir, f"report_{month}.md")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return path
