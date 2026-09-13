"""
Developer B — Server-Side Alert Notifier (Module B5)
====================================================
Provides three functions consumed by backend/app.py's generate_frames():

  maybe_alert(current_severity) -> bool
      Thread-safe transition detector.  Returns True exactly once per
      Low/Medium/None -> High severity transition.  Protects shared state
      with a threading.Lock() so concurrent /video_feed requests from
      multiple browser tabs cannot both fire a spurious alert for the same
      transition event.

  log_alert(event)
      Prints a single formatted console line for every confirmed High
      transition.  Failure-isolated: a malformed event dict never raises.

  send_email_alert(event)
      STUB ONLY — intentional no-op placeholder for a future optional
      SMTP feature.  Not wired into generate_frames() or any route.
      See docstring for integration guidance when the time comes.

Thread-safety design note
--------------------------
Flask's default dev server runs with threaded=True, meaning each incoming
HTTP request is handled in its own thread.  Two browser tabs both loading
/video_feed will run two concurrent generate_frames() generators, both
calling maybe_alert() at nearly the same wall-clock time and therefore
often seeing the same mock_detect() phase.

Without a lock, the following race is possible:

    Thread A reads  _last_severity  → "None"
    Thread B reads  _last_severity  → "None"   ← both see "pre-High" state
    Thread A writes _last_severity  ← "High"
    Thread A returns True  → log fires   (correct)
    Thread B writes _last_severity  ← "High"
    Thread B returns True  → log fires   (spurious duplicate!)

The lock must wrap BOTH the read AND the write as a single atomic critical
section.  Acquiring before the read and releasing after the write is the
only way to prevent a second thread from interleaving its own read between
our read and our write, and thereby seeing stale "pre-transition" state.
"""

import threading

# ---------------------------------------------------------------------------
# Module-level shared state — protected by _lock
# ---------------------------------------------------------------------------

# _lock serialises all access to _last_severity across threads.
_lock = threading.Lock()

# _last_severity tracks the severity value seen at the last call to
# maybe_alert().  Initialised to None so the very first "High" detection
# always counts as a genuine transition (None -> High is not High -> High).
_last_severity: str | None = None


# ---------------------------------------------------------------------------
# maybe_alert — the core transition detector
# ---------------------------------------------------------------------------

def maybe_alert(current_severity: str) -> bool:
    """
    Detect a genuine transition into "High" severity and update stored state.

    Thread-safety: the lock wraps the entire read → compare → write sequence
    as one atomic operation.  See the module docstring for a concrete example
    of the race condition this prevents under Flask's multi-threaded dev server.

    Returns:
        True   — if previous severity was NOT "High" AND current is "High"
                 (a real Low/Medium/None → High transition).
        False  — in every other case:
                   - severity stays High  (repeated High, already alerted)
                   - severity drops from High to anything else
                   - severity is not "High" at all
                 _last_severity is still updated unconditionally on False.

    The unconditional update on both branches is intentional: we always want
    _last_severity to reflect the most recent call, so the NEXT call can
    correctly compare against the current state rather than a stale one.
    """
    global _last_severity

    # Acquire the lock BEFORE reading _last_severity.
    # The entire read-compare-write block is the critical section.
    # No other thread can interleave here between our read and our write.
    with _lock:
        previous = _last_severity           # READ — protected by lock
        is_transition = (                   # COMPARE — still inside lock
            previous != "High"
            and current_severity == "High"
        )
        _last_severity = current_severity   # WRITE — atomic with the read above

    # Return outside the lock — the boolean value is a local variable,
    # no shared state is accessed after this point.
    return is_transition


# ---------------------------------------------------------------------------
# log_alert — console output for confirmed High transitions
# ---------------------------------------------------------------------------

def log_alert(event: dict) -> None:
    """
    Print a formatted alert line to stdout for a confirmed High transition.

    Uses event["timestamp"] and event["confidence"] from the 5-field
    detection contract.  Wrapped in try/except so a malformed or incomplete
    event dict can never propagate an exception to the caller — the video
    stream must remain unaffected by any logging failure.
    """
    try:
        timestamp  = event["timestamp"]
        confidence = float(event["confidence"])
        print(
            f"🔥 ALERT: severity transitioned to HIGH "
            f"at {timestamp} | confidence={confidence:.2f}"
        )
    except Exception as exc:
        # Fallback: still emit something visible in the server log, but
        # never let the exception propagate past this function.
        print(f"🔥 ALERT: severity transitioned to HIGH "
              f"(could not format event details: {exc!r})")


# ---------------------------------------------------------------------------
# send_email_alert — STUB for future optional SMTP feature
# ---------------------------------------------------------------------------

def send_email_alert(event: dict) -> None:
    """
    STUB — intentional no-op.  Do not call this function yet.

    This is a placeholder for the optional SMTP email-alerting feature
    described in the project guide's "Future Improvements" section.  When
    the time comes to implement it, replace `pass` with:

        import smtplib
        from email.mime.text import MIMEText
        # ... build and send the message using project SMTP credentials ...

    IMPORTANT: smtplib and all email-related imports must remain here
    (inside this function or at the top of this file) and NOT be added to
    requirements.txt until the feature is actually activated — this stub
    must remain a zero-dependency no-op.

    This function is deliberately NOT called from generate_frames() or any
    route.  Wire it in (after maybe_alert() returns True) only when SMTP
    credentials and recipient configuration are ready.
    """
    pass  # intentional no-op — see docstring above
