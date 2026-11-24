"""
System metrics collection via /proc and /sys.

Everything here reads kernel files directly (what top/htop/vmstat do under the
hood), with no heavy subprocesses — so Cassandra never competes for resources.
The functions are pure/stateless, which makes them easy to test.
"""

import os

from .constants import PWA_NAMES, SKIP_FILESYSTEMS, USER_CPU_PSI_PATH


def read_psi(resource, path=None):
    """Read /proc/pressure/<resource> (or a cgroup's pressure file via `path`). Returns a dict with some_avg10 and full_avg10.

    PSI (Pressure Stall Information) measures how long tasks were stalled
    waiting for the resource — the most faithful indicator of a real freeze.
    """
    path = path or f"/proc/pressure/{resource}"
    out = {"some_avg10": 0.0, "full_avg10": 0.0}
    try:
        with open(path) as f:
            for line in f:
                parts = line.split()
                kind = parts[0]  # "some" or "full"
                fields = dict(p.split("=") for p in parts[1:])
                out[f"{kind}_avg10"] = float(fields.get("avg10", 0.0))
    except FileNotFoundError:
        pass
    return out


def read_meminfo():
    """Read /proc/meminfo. Returns bytes and percentages for RAM and swap."""
    info = {}
    with open("/proc/meminfo") as f:
        for line in f:
            key, val = line.split(":")
            info[key.strip()] = int(val.strip().split()[0]) * 1024
    total = info.get("MemTotal", 0)
    avail = info.get("MemAvailable", 0)
    swap_total = info.get("SwapTotal", 0)
    swap_free = info.get("SwapFree", 0)
    return {
        "mem_total": total,
        "mem_available": avail,
        "mem_available_pct": (avail / total * 100) if total else 100.0,
        "swap_total": swap_total,
        "swap_free": swap_free,
        "swap_free_pct": (swap_free / swap_total * 100) if swap_total else 100.0,
    }


def read_zram():
    """Sum zram usage over /sys/block/zram*/mm_stat. Returns bytes:
    orig (uncompressed data swapped into zram) and ram (RAM zram itself uses).
    """
    orig = ram = 0
    try:
        devs = [d for d in os.listdir("/sys/block") if d.startswith("zram")]
    except FileNotFoundError:
        devs = []
    for d in devs:
        try:
            with open(f"/sys/block/{d}/mm_stat") as f:
                p = f.read().split()
            orig += int(p[0])
            ram += int(p[2])
        except (FileNotFoundError, ValueError, IndexError):
            pass
    return {"orig": orig, "ram": ram}


def read_cpu_times():
    """Read the aggregate line of /proc/stat. Returns (idle, total)."""
    with open("/proc/stat") as f:
        parts = f.readline().split()[1:]
    nums = list(map(int, parts))
    idle = nums[3] + (nums[4] if len(nums) > 4 else 0)  # idle + iowait
    total = sum(nums)
    return idle, total


def cpu_usage_pct(prev, cur):
    """CPU usage (%) between two read_cpu_times() readings."""
    idle_d = cur[0] - prev[0]
    total_d = cur[1] - prev[1]
    if total_d <= 0:
        return 0.0
    return (1.0 - idle_d / total_d) * 100.0


def read_loadavg():
    """Return (load1, load5, load15) from /proc/loadavg."""
    with open("/proc/loadavg") as f:
        parts = f.read().split()
    return float(parts[0]), float(parts[1]), float(parts[2])


def read_cmdline(pid):
    """Read /proc/<pid>/cmdline (NUL-separated arguments)."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read().decode("utf-8", "replace").split("\0")
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return []


def label_process(name, cmdline):
    """Turn a generic process into a readable, specific label.

    Resolves two cases that confuse diagnosis:
      - Chrome: separates a PWA (Teams/Outlook) from a regular tab, GPU, utility.
      - Java: identifies the real .jar/application (e.g. SonarLint) instead of "java".
    """
    cmd = " ".join(cmdline)

    if "chrome" in name.lower() or "/chrome" in cmd:
        for arg in cmdline:
            if arg.startswith("--app-id="):
                app_id = arg.split("=", 1)[1]
                return PWA_NAMES.get(app_id, f"Chrome PWA ({app_id[:8]}…)")
        if "--type=renderer" in cmd:
            return "Chrome (tab/renderer)"
        if "--type=gpu" in cmd:
            return "Chrome (GPU)"
        if "--type=utility" in cmd:
            return "Chrome (utility)"
        if "--type=" not in cmd:
            return "Chrome (main process)"
        return "Chrome (other)"

    if name.lower() == "java" or "/java" in cmd:
        if "sonarlint" in cmd.lower():
            return "SonarLint (editor extension, Java)"
        for i, arg in enumerate(cmdline):
            if arg == "-jar" and i + 1 < len(cmdline):
                return f"Java: {os.path.basename(cmdline[i + 1])}"
        return "Java (unknown application)"

    return name


def _read_status(pid):
    """Read name, rss (bytes) and ppid from /proc/<pid>/status."""
    name, rss, ppid = "", 0, 0
    with open(f"/proc/{pid}/status") as f:
        for line in f:
            if line.startswith("Name:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("VmRSS:"):
                rss = int(line.split()[1]) * 1024
            elif line.startswith("PPid:"):
                ppid = int(line.split()[1])
    return name, rss, ppid


def top_mem_processes(n=5):
    """Top N processes by RSS, with readable labels. Use only during an incident."""
    procs = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            name, rss, _ = _read_status(pid)
            if rss:
                label = label_process(name, read_cmdline(pid))
                procs.append((rss, label, pid))
        except (FileNotFoundError, ProcessLookupError, PermissionError, ValueError):
            continue
    procs.sort(reverse=True)
    return [
        {"pid": p, "name": nm, "rss_mb": round(r / 1024 / 1024, 1)}
        for r, nm, p in procs[:n]
    ]


def all_pids_rss_ppid():
    """Map pid -> (rss_bytes, ppid) for every process. One pass over /proc."""
    out = {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            _, rss, ppid = _read_status(pid)
            out[int(pid)] = (rss, ppid)
        except (FileNotFoundError, ProcessLookupError, PermissionError, ValueError):
            continue
    return out


def pwa_tree_rss(app_id):
    """Sum the RAM of the ENTIRE process tree of a Chrome PWA (the process with
    the --app-id plus all descendants: renderers, GPU, utility).

    Needed because the renderers (which are the ones that leak) do NOT carry
    the --app-id on their command line — they are children of the main process.
    Returns (total_bytes, n_processes, age_seconds_of_root_process).
    """
    roots = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        cmd = " ".join(read_cmdline(pid))
        # A root is only a REAL Chrome with the app-id (avoids shells/greps that
        # contain the app-id as text and would drag in unrelated trees).
        if f"--app-id={app_id}" not in cmd:
            continue
        if "/chrome" not in cmd and "chromium" not in cmd:
            continue
        roots.append(int(pid))
    if not roots:
        return 0, 0, 0

    table = all_pids_rss_ppid()
    children = {}
    for pid, (_, ppid) in table.items():
        children.setdefault(ppid, []).append(pid)

    seen = set()
    stack = list(roots)
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        stack.extend(children.get(pid, []))

    total = sum(table.get(p, (0, 0))[0] for p in seen)

    age = 0
    try:
        with open("/proc/uptime") as f:
            uptime = float(f.read().split()[0])
        clk = os.sysconf("SC_CLK_TCK")
        oldest = uptime
        for r in roots:
            with open(f"/proc/{r}/stat") as f:
                starttime = int(f.read().split()[21]) / clk
            oldest = min(oldest, uptime - starttime)
        age = int(oldest)
    except (FileNotFoundError, ProcessLookupError, ValueError, IndexError):
        pass

    return total, len(seen), age


def chrome_breakdown():
    """Sum Chrome's RAM by category (PWAs vs tabs vs infrastructure)."""
    cats = {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        cmdline = read_cmdline(pid)
        if not cmdline:
            continue
        cmd = " ".join(cmdline)
        if "/chrome" not in cmd and "chromium" not in cmd:
            continue
        try:
            _, rss, _ = _read_status(pid)
        except (FileNotFoundError, ProcessLookupError, PermissionError, ValueError):
            continue
        if not rss:
            continue
        label = label_process("chrome", cmdline)
        if "PWA" in label or label in PWA_NAMES.values():
            key = "PWAs (Teams/Outlook)"
        elif "tab" in label:
            key = "Browsing tabs"
        else:
            key = "Chrome (infra: GPU/utility/main)"
        cats[key] = cats.get(key, 0) + rss
    return {k: round(v / 1024 / 1024, 1) for k, v in
            sorted(cats.items(), key=lambda x: -x[1])}


def disk_space():
    """Space usage of the real mounted partitions. Returns the highest usage %.

    Ignores pseudo-filesystems and read-only images (snaps), which report
    ~100% by nature and are not a real "disk full" condition.
    """
    worst = {"mount": "/", "used_pct": 0.0}
    seen = set()
    with open("/proc/mounts") as f:
        for line in f:
            dev, mount, fstype = line.split()[:3]
            if not dev.startswith("/dev/"):
                continue
            if dev.startswith("/dev/loop") or fstype in SKIP_FILESYSTEMS:
                continue
            if mount in seen:
                continue
            seen.add(mount)
            try:
                st = os.statvfs(mount)
            except OSError:
                continue
            total = st.f_blocks * st.f_frsize
            free = st.f_bfree * st.f_frsize
            if total == 0:
                continue
            used_pct = (1 - free / total) * 100
            if used_pct > worst["used_pct"]:
                worst = {"mount": mount, "used_pct": round(used_pct, 1)}
    return worst


def _read_proc_cpu_ticks(pid):
    """Read utime+stime (in clock ticks) from /proc/<pid>/stat. Returns int or None.

    The comm field (index 1) may contain spaces/parentheses, so we split after
    the last ')'.
    """
    try:
        with open(f"/proc/{pid}/stat") as f:
            data = f.read()
        after = data[data.rfind(")") + 1:].split()
        # After comm, fields are: state ppid ... ; utime=13th, stime=14th of the
        # full line -> here index 11 and 12 (0-based) of `after`.
        utime = int(after[11])
        stime = int(after[12])
        return utime + stime
    except (FileNotFoundError, ProcessLookupError, PermissionError,
            ValueError, IndexError):
        return None


def top_cpu_processes(n=5, interval=0.3):
    """Top N processes by CPU usage over a short interval.

    CPU is a rate, so we sample each process's CPU ticks twice, `interval`
    seconds apart, and rank by the delta. Cheap: two /proc passes with a short
    sleep. Use only during an incident.
    """
    import time as _time

    total_cpu = os.cpu_count() or 1
    clk = os.sysconf("SC_CLK_TCK")

    def snapshot():
        snap = {}
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            ticks = _read_proc_cpu_ticks(pid)
            if ticks is not None:
                snap[pid] = ticks
        return snap

    first = snapshot()
    _time.sleep(interval)
    second = snapshot()

    procs = []
    for pid, t2 in second.items():
        t1 = first.get(pid)
        if t1 is None:
            continue
        delta_ticks = t2 - t1
        if delta_ticks <= 0:
            continue
        # % of one core = ticks / (clk * interval) * 100; normalize by cores so
        # the number is comparable to the aggregate cpu_usage (0-100 across all).
        pct_all_cores = (delta_ticks / clk / interval) * 100 / total_cpu
        try:
            name, _, _ = _read_status(pid)
            label = label_process(name, read_cmdline(pid))
        except (FileNotFoundError, ProcessLookupError, PermissionError, ValueError):
            continue
        procs.append((pct_all_cores, label, pid))

    procs.sort(reverse=True)
    return [
        {"pid": p, "name": nm, "cpu_pct": round(pct, 1)}
        for pct, nm, p in procs[:n] if pct >= 0.1
    ]


def read_diskstats():
    """Read cumulative sectors read/written across real block devices from
    /proc/diskstats. Returns (read_bytes, write_bytes). Sector = 512 bytes.

    Only counts whole disks (sd*, nvme*n*, vd*, mmcblk*), skipping partitions
    and loop/ram devices, to avoid double counting.
    """
    read_sectors = write_sectors = 0
    try:
        with open("/proc/diskstats") as f:
            for line in f:
                p = line.split()
                if len(p) < 14:
                    continue
                dev = p[2]
                if dev.startswith(("loop", "ram", "dm-")):
                    continue
                # skip partitions: a whole disk name does not end in a digit
                # except nvme (nvme0n1) and mmc (mmcblk0) -> handle those.
                is_partition = (
                    (dev[-1].isdigit() and not dev.startswith(("nvme", "mmcblk")))
                    or ("nvme" in dev and "p" in dev)
                    or ("mmcblk" in dev and "p" in dev))
                if is_partition:
                    continue
                read_sectors += int(p[5])
                write_sectors += int(p[9])
    except (FileNotFoundError, ValueError, IndexError):
        return 0, 0
    return read_sectors * 512, write_sectors * 512


def read_netdev():
    """Read cumulative bytes received/transmitted across real interfaces from
    /proc/net/dev. Returns (rx_bytes, tx_bytes). Skips loopback.
    """
    rx = tx = 0
    try:
        with open("/proc/net/dev") as f:
            for line in f:
                if ":" not in line:
                    continue
                iface, rest = line.split(":", 1)
                iface = iface.strip()
                if iface == "lo":
                    continue
                fields = rest.split()
                rx += int(fields[0])
                tx += int(fields[8])
    except (FileNotFoundError, ValueError, IndexError):
        return 0, 0
    return rx, tx


def machine_info():
    """Stable hardware info, to give scale to the percentages in the reports."""
    info = {"cpu": "?", "cores": os.cpu_count() or 0, "ram_gb": 0, "distro": "?"}
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    info["cpu"] = line.split(":", 1)[1].strip()
                    break
    except OSError:
        pass
    try:
        info["ram_gb"] = round(read_meminfo()["mem_total"] / 1024**3)
    except OSError:
        pass
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    info["distro"] = line.split("=", 1)[1].strip().strip('"')
                    break
    except OSError:
        pass
    return info


def collect_sample(prev):
    """Collect a full "photo" of local resources at this instant.

    Returns (sample, new_prev). `prev` is a state dict carried between calls to
    compute rates (CPU %, disk I/O throughput, network throughput). Pass
    `initial_prev()` on the first call.

    The sample is intentionally a complete snapshot of every resource class —
    CPU, memory, swap, disk (space + I/O), network throughput, load — so an
    incident can be root-caused and correlated after the fact.
    """
    import time as _time

    now = _time.monotonic()
    elapsed = max(1e-6, now - prev["ts"])

    cur_cpu = read_cpu_times()
    mem = read_meminfo()
    zram = read_zram()
    psi_cpu = (read_psi("cpu", USER_CPU_PSI_PATH)
               if os.path.exists(USER_CPU_PSI_PATH) else read_psi("cpu"))
    psi_mem = read_psi("memory")
    psi_io = read_psi("io")
    load1, load5, load15 = read_loadavg()
    disk = disk_space()

    disk_r, disk_w = read_diskstats()
    net_rx, net_tx = read_netdev()
    disk_read_mbs = max(0, disk_r - prev["disk_r"]) / elapsed / 1024**2
    disk_write_mbs = max(0, disk_w - prev["disk_w"]) / elapsed / 1024**2
    net_rx_mbs = max(0, net_rx - prev["net_rx"]) / elapsed / 1024**2
    net_tx_mbs = max(0, net_tx - prev["net_tx"]) / elapsed / 1024**2

    sample = {
        # CPU
        "cpu_usage_pct": cpu_usage_pct(prev["cpu"], cur_cpu),
        "psi_cpu_some_avg10": psi_cpu["some_avg10"],
        "load1": load1,
        "load5": load5,
        "load15": load15,
        # Memory / swap (percent and absolute)
        "mem_available_pct": mem["mem_available_pct"],
        "mem_available_gb": round(mem["mem_available"] / 1024**3, 2),
        "mem_total_gb": round(mem["mem_total"] / 1024**3, 2),
        "swap_free_pct": mem["swap_free_pct"],
        "swap_total": mem["swap_total"],
        "swap_used_gb": round((mem["swap_total"] - mem["swap_free"]) / 1024**3, 2),
        "psi_mem_full_avg10": psi_mem["full_avg10"],
        # zram: compressed swap living in RAM. Demand = RAM in use minus what zram
        # itself occupies, plus the uncompressed size of what it holds: how much
        # memory the workload really asks for, comparable to mem_total_gb.
        "zram_orig_gb": round(zram["orig"] / 1024**3, 2),
        "zram_ram_gb": round(zram["ram"] / 1024**3, 2),
        "mem_demand_gb": round(
            (mem["mem_total"] - mem["mem_available"] - zram["ram"] + zram["orig"])
            / 1024**3, 2),
        # Disk: space + I/O throughput
        "disk_worst_used_pct": disk["used_pct"],
        "disk_mount": disk["mount"],
        "psi_io_full_avg10": psi_io["full_avg10"],
        "disk_read_mbs": round(disk_read_mbs, 2),
        "disk_write_mbs": round(disk_write_mbs, 2),
        # Network throughput (real interfaces, excludes loopback)
        "net_rx_mbs": round(net_rx_mbs, 2),
        "net_tx_mbs": round(net_tx_mbs, 2),
    }
    new_prev = {
        "cpu": cur_cpu, "ts": now,
        "disk_r": disk_r, "disk_w": disk_w,
        "net_rx": net_rx, "net_tx": net_tx,
    }
    return sample, new_prev


def initial_prev():
    """Build the initial `prev` state for the first collect_sample() call."""
    import time as _time
    disk_r, disk_w = read_diskstats()
    net_rx, net_tx = read_netdev()
    return {
        "cpu": read_cpu_times(), "ts": _time.monotonic(),
        "disk_r": disk_r, "disk_w": disk_w,
        "net_rx": net_rx, "net_tx": net_tx,
    }
