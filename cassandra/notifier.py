"""
Desktop notifications (best effort), used only after an incident has passed.

Notifications can carry an "Open report" action. Capturing the click requires
`notify-send --wait`, which blocks until clicked or the notification expires —
so we run it in a DETACHED child process, never blocking the monitor loop. If
the user clicks the action, the child opens the report; otherwise the child
exits on its own when the notification times out.

Degrades gracefully: if notify-send is missing or the notification server does
not support actions, a plain notification is still shown (or nothing happens).
"""

import os
import shutil
import subprocess

from .constants import log

APP_NAME = "Cassandra"
_ACTION_KEY = "open"


def available():
    """True if notify-send is present."""
    return shutil.which("notify-send") is not None


def _plain(summary, body):
    """Fire-and-forget notification with no action button."""
    try:
        subprocess.Popen(
            ["notify-send", "-a", APP_NAME, summary, body],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        log.debug("notify-send failed: %s", exc)


def notify(summary, body):
    """Simple post-incident notification, no action."""
    if available():
        _plain(summary, body)


def notify_with_open(summary, body, file_path, open_command="xdg-open",
                     timeout_ms=20000):
    """Notification with an 'Open report' action that opens `file_path`.

    Runs a detached child that waits for the click without blocking the caller.
    Falls back to a plain notification if notify-send is unavailable.

    `timeout_ms` is how long the notification stays on screen (0 = until
    dismissed). Uses normal urgency (not critical) so it is not intrusive; a
    generous timeout gives time to reveal/click the action, since GNOME Shell
    only shows action buttons on hover/expand.
    """
    if not available():
        return
    if not file_path or not os.path.exists(file_path):
        _plain(summary, body)
        return

    # Child shell: show the notification with an action and wait for the click.
    # We register TWO actions:
    #   - "default": fired when the user clicks the notification BODY (some
    #     desktops, e.g. GNOME, do not report body clicks — harmless if unused).
    #   - our open key: the explicit "Open report" button.
    # Either one opens the file. Everything is detached (Popen, no wait) so the
    # monitor loop is never blocked.
    #
    # Normal urgency + a configurable timeout (`$7`, in ms). GNOME Shell only
    # reveals action buttons on hover/expand, so the timeout should be generous
    # enough to reveal and click; set it to 0 in config for a persistent one.
    #
    # Note: the values are passed as argv (not interpolated into the shell
    # string) to avoid any quoting/injection issues with the path.
    script = (
        'chosen="$(notify-send -a "$1" -t "$7" --wait '
        '--action="default=Open report" --action="$2=Open report" '
        '"$3" "$4" 2>/dev/null)"; '
        'case "$chosen" in "$2"|default) exec "$5" "$6";; esac'
    )
    try:
        subprocess.Popen(
            ["sh", "-c", script, "sh",
             APP_NAME, _ACTION_KEY, summary, body, open_command, file_path,
             str(int(timeout_ms))],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
    except OSError as exc:
        log.debug("notify-send (action) failed: %s", exc)
        _plain(summary, body)
