"""
Network probes for the corporate path (VPN/ZTNA).

Compares the latency to a public target against the response time of the
monitored services, to separate blame between the user's local internet and
the company's infrastructure. Supports ZTNA scenarios (FortiClient), where
there is no tunnel interface and the DNS of corporate domains is rewritten to
internal IPs.

These functions use lightweight subprocesses (curl/ping/getent/pgrep), so they
run on a larger interval than the resource sampling.
"""

import subprocess

from .constants import SUBPROCESS_TIMEOUT, log


def resolve_host(host):
    """Resolve via getent (respects the FortiClient/ZTNA resolver).

    Returns the IP or an empty string. A 10.x IP usually indicates DNS
    rewritten by ZTNA (traffic going through the company path).
    """
    try:
        out = subprocess.run(
            ["getent", "hosts", host],
            capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.split()[0]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return ""


def process_alive(process_match):
    """True if there is a process whose command line matches process_match."""
    try:
        out = subprocess.run(
            ["pgrep", "-f", process_match],
            capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT,
        )
        return out.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def http_probe(url, timeout):
    """Probe an HTTPS URL with curl, measuring timings. Returns a metrics dict."""
    fmt = "%{time_connect} %{time_starttransfer} %{time_total} %{http_code}"
    fail = {"ok": False, "connect": None, "ttfb": None, "total": None,
            "http_code": None}
    try:
        out = subprocess.run(
            ["curl", "-so", "/dev/null", "-w", fmt,
             "--max-time", str(timeout), url],
            capture_output=True, text=True, timeout=timeout + 3,
        )
        if out.returncode != 0 or not out.stdout.strip():
            return {**fail, "error": f"curl rc={out.returncode}"}
        c, t, tot, code = out.stdout.split()
        return {"ok": True, "connect": float(c), "ttfb": float(t),
                "total": float(tot), "http_code": int(code), "error": None}
    except subprocess.TimeoutExpired:
        return {**fail, "error": "timeout"}
    except FileNotFoundError:
        return {**fail, "error": "curl not found"}


def ping_rtt(host):
    """Average RTT (ms) to a public host. Returns a float or None."""
    try:
        out = subprocess.run(
            ["ping", "-c", "3", "-w", "4", host],
            capture_output=True, text=True, timeout=8,
        )
        for line in out.stdout.splitlines():
            if "rtt" in line or "round-trip" in line:
                return float(line.split("=")[1].split("/")[1])
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError, IndexError):
        pass
    return None


def collect(targets, public_ref, ztna_process, timeout):
    """Collect the full network state.

    - targets: list of (name, url) to probe over HTTPS.
    - public_ref: public host for the baseline RTT (e.g. 8.8.8.8).
    - ztna_process: command of the ZTNA proxy to check (may be empty).
    - timeout: HTTP timeout in seconds.
    """
    result = {
        "ztna_active": process_alive(ztna_process) if ztna_process else None,
        "public_ref_rtt_ms": ping_rtt(public_ref) if public_ref else None,
        "targets": {},
    }
    for name, url in targets:
        host = url.split("://", 1)[-1].split("/")[0]
        probe = http_probe(url, timeout)
        probe["resolved_ip"] = resolve_host(host)
        probe["via_ztna_dns"] = probe["resolved_ip"].startswith("10.")
        result["targets"][name] = probe
    log.debug("network collected: ztna=%s ref=%sms targets=%d",
              result["ztna_active"], result["public_ref_rtt_ms"],
              len(result["targets"]))
    return result
