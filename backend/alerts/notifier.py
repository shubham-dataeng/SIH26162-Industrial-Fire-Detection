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
      transition and dispatches a real Twilio SMS if SMS_CONFIGURED is True.
      Failure-isolated: a malformed event dict or SMS failure never raises
      and never breaks the console log.

  send_email_alert(event)
      STUB ONLY — intentional no-op placeholder for a future optional
      SMTP feature.  Not wired into generate_frames() or any route.
      See docstring for integration guidance when the time comes.

SMS alerting (added 2026-09-13)
--------------------------------
Twilio SMS is sent on every Low/Medium/None → High transition, exactly
once per transition (same gate as the existing console log).  Credentials
are loaded from a .env file at the repo root via python-dotenv.  If any
of the four required keys (TWILIO_SID, TWILIO_TOKEN, TWILIO_FROM, ALERT_TO)
are absent or empty, SMS is silently disabled and console logging is
completely unaffected.

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

import os
import threading

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Load .env from the repo root (two levels up from this file:
#   backend/alerts/notifier.py → backend/alerts/ → backend/ → repo root)
# override=False so real environment variables already set take precedence.
# ---------------------------------------------------------------------------
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(_REPO_ROOT, ".env"), override=False)

# ---------------------------------------------------------------------------
# SMS configuration — read once at import time, never re-read per alert.
# ---------------------------------------------------------------------------
_TWILIO_SID   = os.environ.get("TWILIO_SID",   "").strip()
_TWILIO_TOKEN = os.environ.get("TWILIO_TOKEN",  "").strip()
_TWILIO_FROM  = os.environ.get("TWILIO_FROM",   "").strip()
_ALERT_TO     = os.environ.get("ALERT_TO",      "").strip()

# True only if every required key is present and non-empty.
SMS_CONFIGURED: bool = all([_TWILIO_SID, _TWILIO_TOKEN, _TWILIO_FROM, _ALERT_TO])

# Report startup status — presence only, never credential values.
if SMS_CONFIGURED:
    print("[notifier] SMS alerting ACTIVE via Twilio "
          "(TWILIO_SID=PRESENT, TWILIO_TOKEN=PRESENT, "
          "TWILIO_FROM=PRESENT, ALERT_TO=PRESENT)")
else:
    _missing = [k for k, v in {
        "TWILIO_SID": _TWILIO_SID, "TWILIO_TOKEN": _TWILIO_TOKEN,
        "TWILIO_FROM": _TWILIO_FROM, "ALERT_TO": _ALERT_TO,
    }.items() if not v]
    print(f"[notifier] SMS alerting DISABLED — missing or empty keys: {_missing}")


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
# maybe_alert — the core transition detector (UNCHANGED from original)
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
# send_sms_alert — Twilio SMS dispatch for confirmed High transitions
# ---------------------------------------------------------------------------

def send_sms_alert(event: dict) -> None:
    """
    Send a Twilio SMS for a confirmed High-severity transition.

    No-ops immediately if SMS_CONFIGURED is False (missing .env keys).
    Must never raise — any exception is caught and printed here so that
    the caller (log_alert) is never disrupted.

    Parameters
    ----------
    event : dict
        The 5-field detection contract dict:
        {"timestamp", "class", "confidence", "bbox", "severity"}
    """
    if not SMS_CONFIGURED:
        return

    try:
        confidence = float(event.get("confidence", 0))
        timestamp  = event.get("timestamp", "unknown")

        body = (
            f"🔥 FIRE ALERT - SIH26162\n"
            f"Severity: HIGH\n"
            f"Confidence: {confidence * 100:.0f}%\n"
            f"Time: {timestamp}\n"
            f"Action Required: Evacuate and contact emergency services."
        )

        from twilio.rest import Client  # import here keeps startup fast when disabled
        client = Client(_TWILIO_SID, _TWILIO_TOKEN)
        message = client.messages.create(
            body=body,
            from_=_TWILIO_FROM,
            to=_ALERT_TO,
        )
        print(f"[notifier] SMS sent successfully (SID: {message.sid})")

    except Exception as exc:
        # Check for Twilio's unverified-recipient error specifically.
        exc_str = str(exc)
        if "unverified" in exc_str.lower() or "21608" in exc_str:
            print(
                "[notifier] SMS failed: recipient number is not verified in your "
                "Twilio trial account — verify it at https://console.twilio.com"
            )
        else:
            # Generic failure: show type + short reason, never credential values.
            print(f"[notifier] SMS failed: {type(exc).__name__} — {exc_str[:120]}")


# ---------------------------------------------------------------------------
# log_alert — console output + SMS for confirmed High transitions
# ---------------------------------------------------------------------------

def log_alert(event: dict) -> None:
    """
    Print a formatted alert line to stdout for a confirmed High transition,
    then attempt to send an SMS via send_sms_alert().

    The SMS call is wrapped in its own try/except so any SMS failure can
    never silence or break the console log that was already working.

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

    # SMS dispatch — isolated so it can never affect the console log above.
    try:
        send_sms_alert(event)
    except Exception as exc:
        print(f"[notifier] Unexpected error in send_sms_alert: {exc!r}")


# ---------------------------------------------------------------------------
# send_email_alert — STUB for future optional SMTP feature (UNCHANGED)
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
