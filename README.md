# Cassandra 🔮

> *In Greek myth, Cassandra was given the gift of foreseeing the future — but
> cursed never to be believed. She saw disaster coming and no one listened.*

**Cassandra** is a lightweight resource monitor for Linux that does exactly
that: it sees your machine's bottlenecks coming (CPU, RAM, swap, disk, network)
and **records the evidence** — so that, when you tell the IT team your machine
freezes, you are not the Cassandra nobody believes. You will have the reports.

It runs quietly in the background, detects real freezes and slowness (not just
"high usage"), and produces per-incident reports plus a monthly summary — ready
to justify a hardware upgrade or an infrastructure improvement.

The tool's command is `cassie`.

---

## Why it never becomes a bottleneck itself

A monitor that consumes resources to measure the lack of resources would be
useless. Cassandra is obsessed with being lightweight:

- Lowest priority: `Nice=19`, CPU and I/O in the `idle` class (uses only what
  nobody else wants).
- Bounded by cgroup: at most ~150 MB of RAM and ~5% of one CPU.
- In practice it uses ~8–15 MB of RAM and less than 1% CPU.
- Reads `/proc` and `/sys` directly (like `top`/`htop`), with no heavy
  subprocesses in the fast loop. During a bottleneck it only writes a few bytes.

### "If it yields resources, can it measure during the freeze?"

Yes. The primary trigger is the kernel's **PSI (Pressure Stall Information)**
(`/proc/pressure/{cpu,memory,io}`), which measures **how long tasks were stalled
waiting** for each resource — continuously and retroactively, regardless of
whether Cassandra was active at that instant. When it gets to read, the freeze
is already recorded by the kernel. It is the most faithful "the machine froze
for lack of a resource" signal available on modern Linux.

---

## Installation

Requirements: Linux with systemd, Python 3, `curl` and `ping` (standard on most
distros). Kernel with PSI enabled (default on Ubuntu and recent distros).

```bash
git clone https://github.com/NathanAlcantara/cassandra.git
cd cassandra
./cassie install      # installs and enables the user service
./cassie status       # confirm it is watching
```

`cassie install` also creates your local `config.ini` and `CONTEXT.md` from the
`*.example` templates (only if they don't exist yet). Both are gitignored: they
describe your machine and workplace, so they never leave it. Edit them to match
your environment (see [The reports](#the-reports) and
[Configuration](#configuration)).

> Tip: to call `cassie` from anywhere, add the directory to your PATH or create
> a symlink: `ln -s "$PWD/cassie" ~/.local/bin/cassie`.

---

## Usage

```bash
cassie start            # start monitoring
cassie stop             # stop
cassie restart          # restart (applies config.ini changes)
cassie status           # is it running? how much does it use?
cassie logs             # follow the logs
cassie incidents        # list recorded incidents
cassie report           # generate the current month's report
cassie report 2026-09   # report for a specific month
cassie clear            # wipe all collected data
cassie config           # open config.ini in the editor
cassie help             # help
```

You can also drive it directly via Python:

```bash
python3 run.py --help
python3 -m cassandra report 2026-09
```

---

## What it detects

An incident is only recorded when the condition is **sustained** (~30 s, to
ignore harmless spikes). The optional desktop notification only appears **after**
the machine recovers — never during the freeze, so it does not compete for
resources at the worst moment. Network-only incidents (company infrastructure)
are recorded in the reports but do not notify.

| Resource | Trigger (tunable in `config.ini`) |
|---|---|
| Memory | available RAM < 6%, or free swap < 10%, or PSI memory(full) > 5 |
| CPU | usage > 90%, or PSI cpu(some) of the user session > 25 |
| Disk I/O | PSI io(full) > 20 |
| Disk space | real partition > 90% (snaps/loop are ignored) |
| Network | monitored services fail or take > 3 s to first byte, on 2 consecutive probes (~1 min) |

---

## Notifications

When an incident ends, Cassandra sends a desktop notification (via
`notify-send`) — always **after** the machine recovers, never during the freeze.
The notification carries an **"Open report"** action button; clicking it opens
that incident's markdown report.

Design notes:

- The click is handled by a detached child process, so waiting for it **never
  blocks** the monitor loop. If you ignore the notification, nothing opens.
- The notification uses **normal urgency** and stays on screen for a configurable
  time (`notification_timeout_ms`, default 20s; set `0` to keep it until
  dismissed). **GNOME Shell only reveals action buttons on hover or when the
  notification is expanded** — so hover over the notification (or open the tray
  with `Super+V`) to reveal the **"Open report"** button. Clicking the
  notification **body** also opens the report.
- It degrades gracefully: if `notify-send` is missing, or the notification
  server does not support action buttons, a plain notification is still shown.
- Disable notifications entirely with `notify_after_incident = false` in
  `config.ini`.

### Choosing how the report opens

Clicking "Open report" runs the `open_command` from `config.ini` (default
`xdg-open`, which uses the system's default app):

```ini
[general]
open_command = xdg-open
```

`xdg-open` relies on a default application being associated with markdown. If
`.md` files have no default app on your system, either:

- **Fix it at the source** (recommended) — associate markdown with an app once:
  ```bash
  # example: open .md files with GNOME Text Editor
  xdg-mime default org.gnome.TextEditor.desktop text/markdown
  ```
- **Or set a specific command** in `config.ini`, bypassing `xdg-open`:
  ```ini
  open_command = code              # VS Code (renders a markdown preview)
  # open_command = gnome-text-editor
  ```

---

## The reports

- **Per incident** (`reports/incidents/incident_N.md`): environment/hardware,
  automatic diagnosis (e.g. it tells memory *thrashing* apart from a slow disk),
  reasons, resource peak, the processes using the most RAM, and network state.
- **Monthly** (`reports/report_YYYY-MM.md`): executive summary, incidents by
  category, distribution by hour, memory and network analysis.
- **`CONTEXT.md`**: your environment briefing, copied alongside the report. You
  write it once from `CONTEXT.md.example` (hardware, process glossary, how you
  reach company services); it stays local and gitignored. Send
  this file **together** with the reports when analyzing with an AI or handing
  them to IT — it holds the context (hardware, PSI metric meaning, process
  glossary, network) that makes the analysis accurate rather than generic.

To export as PDF:

```bash
sudo apt install pandoc
pandoc reports/report_2026-09.md -o report_2026-09.pdf
```

---

## Configuration

Everything is tunable in `config.ini` (commented fields; created from
`config.ini.example`, kept local). After editing, run
`cassie restart`. Highlights:

- `sample_interval_sec` / `network_interval_sec`: collection frequency.
- `sustained_samples`: how many consecutive bad samples define an incident.
- Sections `[cpu] [memory] [disk] [network]`: each resource's thresholds.
- `[network] targets`: the HTTPS services you want to monitor.
- `[pwa_watch]`: optional watcher for a web app (PWA) that leaks memory.
- `notify_after_incident` / `open_command`: post-incident desktop notification
  and the command used by its "Open report" action (see [Notifications](#notifications)).

### Network monitoring

Cassandra compares the latency to a public target (e.g. `8.8.8.8`) with the
response time of your services to **separate blame**: if local internet is fine
and the machine is idle, but a corporate service is slow, the bottleneck is in
the infrastructure, not your device. It supports VPN and ZTNA scenarios
(`CONTEXT.md` documents how to adapt it to your environment).

---

## Architecture

Cassandra is a small Python package (standard library only):

```
cassandra/
├── constants.py   # defaults, thresholds, shared logger
├── metrics.py     # /proc & /sys readers (PSI, memory, CPU, disk, processes)
├── network.py     # ZTNA/VPN probes (curl, ping, DNS, proxy state)
├── incidents.py   # threshold evaluation, incident state machine, diagnosis
├── config.py      # config.ini -> typed objects
├── storage.py     # SQLite persistence
├── report.py      # per-incident + monthly markdown, CONTEXT.md
├── monitor.py     # the daemon loop
└── cli.py         # argparse entry point
run.py             # `python3 run.py <command>`
cassie             # shell wrapper for the systemd service + reports
cassandra.service  # systemd (user) unit with the leanness guarantees
config.ini.example # thresholds and options (copy to config.ini)
CONTEXT.md.example # environment briefing template (copy to CONTEXT.md)
```

Run the tests with:

```bash
pip install -r requirements-dev.txt
python3 -m pytest
```

---

## Privacy

Cassandra runs 100% locally. It sends nothing anywhere. Reports may contain
process names and domains from your machine — which is why they are gitignored
by default (see `.gitignore`). Review before sharing.

---

## Contributing

Contributions are welcome. The tool was born on Ubuntu with FortiClient ZTNA,
but it is designed to be generic. Useful ideas:

- Support/tests on other distros and network environments.
- New process labels in the glossary (`label_process` in `cassandra/metrics.py`).
- Additional report formats (HTML, JSON).

Open an issue or a pull request. Commits follow the Conventional Commits
standard (enforced by `lefthook`).

## License

MIT — see [LICENSE](LICENSE).
