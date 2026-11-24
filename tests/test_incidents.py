from cassandra import incidents
from cassandra.incidents import IncidentTracker

from .conftest import make_sample


# ---------------- evaluate ----------------


def test_healthy_sample_has_no_reasons(thresholds):
    assert incidents.evaluate(make_sample(), thresholds) == []


def test_low_ram_is_flagged(thresholds):
    reasons = incidents.evaluate(make_sample(mem_available_pct=3.0), thresholds)
    assert any("RAM" in r for r in reasons)


def test_exhausted_swap_is_flagged(thresholds):
    # Low swap WITH low RAM -> flagged
    reasons = incidents.evaluate(
        make_sample(swap_free_pct=1.0, mem_available_pct=5.0), thresholds)
    assert any("swap" in r.lower() for r in reasons)


def test_low_swap_alone_is_not_flagged(thresholds):
    # Low swap but plenty of RAM and no memory PSI -> NOT an incident
    # (this was a false-positive source: swap stays occupied by old pages).
    reasons = incidents.evaluate(
        make_sample(swap_free_pct=6.0, mem_available_pct=40.0,
                    psi_mem_full_avg10=0.0), thresholds)
    assert not any("swap" in r.lower() for r in reasons)


def test_low_swap_with_memory_psi_is_flagged(thresholds):
    # Low swap accompanied by elevated memory PSI -> flagged even if RAM % is ok
    reasons = incidents.evaluate(
        make_sample(swap_free_pct=6.0, mem_available_pct=40.0,
                    psi_mem_full_avg10=12.0), thresholds)
    assert any("swap" in r.lower() for r in reasons)


def test_swap_ignored_when_no_swap(thresholds):
    # swap_total == 0 must not raise a swap reason even with 0% free
    reasons = incidents.evaluate(
        make_sample(swap_total=0, swap_free_pct=0.0), thresholds)
    assert not any("swap" in r.lower() for r in reasons)


def test_memory_pressure_psi_flagged(thresholds):
    reasons = incidents.evaluate(make_sample(psi_mem_full_avg10=12.0), thresholds)
    assert any("Memory pressure" in r for r in reasons)


def test_disk_io_flagged(thresholds):
    reasons = incidents.evaluate(make_sample(psi_io_full_avg10=40.0), thresholds)
    assert any("Disk saturated" in r for r in reasons)


# ---------------- evaluate_network ----------------


def test_network_failure_flagged():
    net = {"ztna_active": True, "targets": {
        "github": {"ok": False, "error": "timeout", "ttfb": None}}}
    reasons = incidents.evaluate_network(net, http_ttfb_warn=3.0)
    assert any("github" in r and "failure" in r for r in reasons)


def test_network_slow_flagged():
    net = {"ztna_active": True, "targets": {
        "maven": {"ok": True, "ttfb": 4.0, "total": 9.0}}}
    reasons = incidents.evaluate_network(net, http_ttfb_warn=3.0)
    assert any("slow" in r for r in reasons)


def test_network_ok_no_reasons():
    net = {"ztna_active": True, "targets": {
        "github": {"ok": True, "ttfb": 0.5, "total": 2.0}}}
    assert incidents.evaluate_network(net, http_ttfb_warn=3.0) == []


def test_network_slow_body_download_not_flagged():
    # Fast TTFB but long total = big page body, not a slow path.
    net = {"ztna_active": True, "targets": {
        "github": {"ok": True, "ttfb": 0.5, "total": 8.0}}}
    assert incidents.evaluate_network(net, http_ttfb_warn=3.0) == []


def test_ztna_down_flagged():
    net = {"ztna_active": False, "targets": {}}
    reasons = incidents.evaluate_network(net, http_ttfb_warn=3.0)
    assert any("ZTNA" in r for r in reasons)


# ---------------- categorize ----------------


def test_categorize_memory_and_disk():
    cat = incidents.categorize(["Available RAM 3% (< 6%)", "Disk / 95% full"], [])
    assert "memory" in cat and "disk" in cat


def test_categorize_network():
    assert "network" in incidents.categorize([], ["github: connection failure"])


# ---------------- summarize_reasons ----------------


def test_summarize_groups_repeats():
    reasons = [
        "Memory pressure (PSI full 5.1)",
        "Memory pressure (PSI full 40.0)",
        "Memory pressure (PSI full 12.0)",
    ]
    out = incidents.summarize_reasons(reasons)
    assert len(out) == 1
    assert "3x" in out[0]
    assert "5.1" in out[0] and "40" in out[0]


# ---------------- IncidentTracker ----------------


def test_tracker_opens_and_closes():
    tr = IncidentTracker(sustained_samples=2, recovery_samples=2)
    bad = make_sample(mem_available_pct=3.0, swap_free_pct=1.0)
    good = make_sample()

    # two bad samples open the incident
    assert tr.update(bad, ["Available RAM 3% (< 6%)"], [], None) is None
    assert tr.update(bad, ["Available RAM 3% (< 6%)"], [], None) is None
    assert tr.active is not None

    # two good samples close it
    assert tr.update(good, [], [], None) is None
    closed = tr.update(good, [], [], None)
    assert closed is not None
    assert closed["category"] == "memory"
    assert closed["duration_sec"] >= 0
    assert "top_procs" in closed


def test_tracker_ignores_isolated_spike():
    tr = IncidentTracker(sustained_samples=3, recovery_samples=2)
    # a single bad sample must not open an incident
    tr.update(make_sample(mem_available_pct=3.0), ["Available RAM 3% (< 6%)"], [], None)
    assert tr.active is None


def test_tracker_category_covers_whole_incident():
    # Opens on a network failure, then RAM runs out: category must say both.
    tr = IncidentTracker(sustained_samples=1, recovery_samples=1)
    net = {"targets": {"github": {"ok": False}}}
    tr.update(make_sample(), [], ["github: connection failure (curl rc=28)"], net)
    tr.update(make_sample(mem_available_pct=2.0), ["Available RAM 2% (< 6%)"], [], net)
    closed = tr.update(make_sample(), [], [], net)
    assert closed["category"] == "memory+network"


def test_tracker_peak_is_worst_of_each_metric():
    tr = IncidentTracker(sustained_samples=1, recovery_samples=1)
    tr.update(make_sample(mem_available_pct=4.0, psi_cpu_some_avg10=10.0), ["x RAM"], [], None)
    tr.update(make_sample(mem_available_pct=5.0, psi_cpu_some_avg10=80.0), ["CPU"], [], None)
    closed = tr.update(make_sample(), [], [], None)
    assert closed["peak"]["mem_available_pct"] == 4.0
    assert closed["peak"]["psi_cpu_some_avg10"] == 80.0


# ---------------- interpret ----------------


def test_interpret_thrashing():
    inc = {
        "peak": make_sample(mem_available_pct=3.0, swap_free_pct=0.0,
                            psi_mem_full_avg10=40.0, psi_io_full_avg10=70.0),
        "net": None,
    }
    notes = incidents.interpret(inc)
    assert any("thrashing" in n.lower() for n in notes)


def test_interpret_network_consequence_when_mem_starved():
    inc = {
        "peak": make_sample(mem_available_pct=3.0, swap_free_pct=0.0,
                            psi_mem_full_avg10=40.0),
        "net": {"public_ref_rtt_ms": 15.0,
                "targets": {"github": {"ok": False}}},
    }
    notes = incidents.interpret(inc)
    assert any("consequence" in n.lower() for n in notes)


def test_interpret_network_infra_when_healthy_local():
    inc = {
        "peak": make_sample(),  # healthy machine
        "net": {"public_ref_rtt_ms": 15.0,
                "targets": {"github": {"ok": False}}},
    }
    notes = incidents.interpret(inc)
    assert any("infrastructure" in n.lower() for n in notes)


def test_interpret_flags_demand_above_physical_ram():
    inc = {"peak": make_sample(mem_demand_gb=21.0, mem_total_gb=15.3,
                               zram_orig_gb=6.0), "net": None}
    notes = incidents.interpret(inc)
    assert any("exceeded" in n and "21.0 GB" in n for n in notes)


def test_interpret_no_demand_note_when_it_fits():
    inc = {"peak": make_sample(mem_demand_gb=9.0, mem_total_gb=15.3), "net": None}
    assert not any("exceeded" in n for n in incidents.interpret(inc))


def test_tracker_interrupt_closes_at_last_sample():
    # Suspend/shutdown: the incident ends at the last observed sample, not when
    # monitoring resumes hours later.
    tr = IncidentTracker(sustained_samples=1, recovery_samples=3)
    tr.update(make_sample(mem_available_pct=2.0), ["Available RAM 2% (< 6%)"], [], None)
    last = incidents.now_iso()
    closed = tr.interrupt(last)
    assert closed["end_ts"] == last
    assert tr.active is None and tr.bad_streak == 0
    assert tr.interrupt(last) is None
