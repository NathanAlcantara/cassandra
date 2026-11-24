from cassandra import metrics


# ---------------- cpu_usage_pct ----------------


def test_cpu_usage_all_idle():
    # no change in busy time -> 0% usage
    prev = (100, 200)   # idle=100, total=200
    cur = (200, 300)    # idle +100, total +100 -> fully idle
    assert metrics.cpu_usage_pct(prev, cur) == 0.0


def test_cpu_usage_all_busy():
    prev = (100, 200)
    cur = (100, 300)    # idle unchanged, total +100 -> 100% busy
    assert metrics.cpu_usage_pct(prev, cur) == 100.0


def test_cpu_usage_no_delta():
    assert metrics.cpu_usage_pct((100, 200), (100, 200)) == 0.0


# ---------------- label_process ----------------


def test_label_chrome_pwa_known():
    label = metrics.label_process(
        "chrome",
        ["/opt/google/chrome/chrome",
         "--app-id=cifhbcnohmdccbgoicgdjpfamggdegmo"])
    assert label == "Teams (PWA)"


def test_label_chrome_renderer():
    label = metrics.label_process(
        "chrome", ["/opt/google/chrome/chrome", "--type=renderer"])
    assert label == "Chrome (tab/renderer)"


def test_label_sonarlint():
    label = metrics.label_process(
        "java", ["/usr/bin/java", "-jar", "/tmp/sonarlint-ls.jar"])
    assert "SonarLint" in label


def test_label_generic_java_jar():
    label = metrics.label_process("java", ["/usr/bin/java", "-jar", "/opt/app/foo.jar"])
    assert label == "Java: foo.jar"


def test_label_passthrough():
    assert metrics.label_process("bash", ["/bin/bash"]) == "bash"


# ---------------- machine_info (integration-ish, reads real /proc) ----------------


def test_machine_info_has_fields():
    info = metrics.machine_info()
    assert set(info) == {"cpu", "cores", "ram_gb", "distro"}
    assert info["cores"] >= 1


# ---------------- top_cpu_processes (integration-ish, reads real /proc) ----------------


def test_top_cpu_processes_shape():
    # Burn a little CPU in the background so at least this process shows up.
    procs = metrics.top_cpu_processes(n=5, interval=0.2)
    assert isinstance(procs, list)
    assert len(procs) <= 5
    for p in procs:
        assert set(p) == {"pid", "name", "cpu_pct"}
        assert p["cpu_pct"] >= 0.1


# ---------------- diskstats / netdev readers ----------------


def test_read_diskstats_returns_bytes():
    r, w = metrics.read_diskstats()
    assert isinstance(r, int) and isinstance(w, int)
    assert r >= 0 and w >= 0


def test_read_netdev_returns_bytes():
    rx, tx = metrics.read_netdev()
    assert isinstance(rx, int) and isinstance(tx, int)
    assert rx >= 0 and tx >= 0


# ---------------- collect_sample: the full "photo" ----------------


def test_collect_sample_is_a_complete_photo():
    prev = metrics.initial_prev()
    import time
    time.sleep(0.1)
    sample, new_prev = metrics.collect_sample(prev)
    # every resource class must be present, so an incident can be root-caused
    expected = {
        "cpu_usage_pct", "psi_cpu_some_avg10", "load1", "load5", "load15",
        "mem_available_pct", "mem_available_gb", "mem_total_gb",
        "swap_free_pct", "swap_total", "swap_used_gb", "psi_mem_full_avg10",
        "disk_worst_used_pct", "disk_mount", "psi_io_full_avg10",
        "disk_read_mbs", "disk_write_mbs", "net_rx_mbs", "net_tx_mbs",
    }
    assert expected.issubset(set(sample))
    # new_prev can be fed back in
    assert {"cpu", "ts", "disk_r", "disk_w", "net_rx", "net_tx"} <= set(new_prev)
