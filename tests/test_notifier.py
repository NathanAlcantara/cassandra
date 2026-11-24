from cassandra import notifier


def test_notify_noop_without_notify_send(monkeypatch):
    # When notify-send is absent, notify() must be a safe no-op (no exception).
    monkeypatch.setattr(notifier.shutil, "which", lambda _: None)
    notifier.notify("summary", "body")  # should not raise


def test_notify_with_open_falls_back_when_file_missing(monkeypatch):
    # notify-send present, but the file does not exist -> plain notification,
    # never the action variant.
    monkeypatch.setattr(notifier.shutil, "which", lambda _: "/usr/bin/notify-send")
    calls = {"plain": 0}
    monkeypatch.setattr(notifier, "_plain",
                        lambda *a, **k: calls.__setitem__("plain", calls["plain"] + 1))
    notifier.notify_with_open("s", "b", "/nonexistent/incident_99.md")
    assert calls["plain"] == 1


def test_notify_with_open_noop_without_notify_send(monkeypatch, tmp_path):
    monkeypatch.setattr(notifier.shutil, "which", lambda _: None)
    f = tmp_path / "incident_1.md"
    f.write_text("x")
    # Must not raise even with a valid file when notify-send is missing.
    notifier.notify_with_open("s", "b", str(f))


def test_notify_with_open_spawns_child(monkeypatch, tmp_path):
    # With notify-send present and a real file, it should spawn a detached child
    # via Popen (not block). We capture the Popen args.
    monkeypatch.setattr(notifier.shutil, "which", lambda _: "/usr/bin/notify-send")
    captured = {}

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(notifier.subprocess, "Popen", fake_popen)
    f = tmp_path / "incident_1.md"
    f.write_text("x")
    notifier.notify_with_open("Title", "Body", str(f), open_command="xdg-open")

    # Detached (new session) and carries the file path + open command in argv.
    assert captured["kwargs"].get("start_new_session") is True
    assert str(f) in captured["args"]
    assert "xdg-open" in captured["args"]
