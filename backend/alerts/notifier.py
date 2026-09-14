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

  log_alert(event, report_path=None)
      Prints a single formatted console line for every confirmed High
      transition and dispatches alerts via all configured channels
      (Telegram Bot message, optional Telegram document attachment, and/or Fast2SMS SMS).
      Failure-isolated: a malformed event dict or failure in any notification
      channel never raises, never affects the other channel, and never breaks
      the console log.

  send_telegram_document(file_path, caption=None)
      Sends a file (e.g. incident report PDF) as a Telegram document attachment.
      Failure-isolated: never raises.

  send_email_alert(event)
      STUB ONLY — intentional no-op placeholder for a future optional
      SMTP feature.  Not wired into generate_frames() or any route.
      See docstring for integration guidance when the time comes.

Multi-channel alerting (Telegram + Fast2SMS) (updated 2026-09-14)
-----------------------------------------------------------------
Alerts are sent on every Low/Medium/None → High transition, exactly once per
transition (same gate as the existing console log). Credentials are loaded
from a .env file at the repo root via python-dotenv:
  - Telegram: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  - Fast2SMS: FAST2SMS_API_KEY, ALERT_PHONE_NUMBER

Each channel is independently configured and failure-isolated:
  1. Telegram Bot API: POST https://api.telegram.org/bot<TOKEN>/sendMessage
     Sends alert message JSON with a 10-second timeout. Checks {"ok": true}.
  2. Fast2SMS Quick SMS: GET https://www.fast2sms.com/dev/bulkV2
     Sends alert SMS via route="q" with a 10-second timeout. Checks {"return": true}.

If credentials for a channel are absent or empty, that channel is disabled while
any configured channel and console logging continue unaffected.

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

import requests as _requests      # already in requirements.txt; used by send_sms_alert
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Load .env from the repo root (two levels up from this file:
#   backend/alerts/notifier.py → backend/alerts/ → backend/ → repo root)
# override=False so real environment variables already set take precedence.
# ---------------------------------------------------------------------------
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(_REPO_ROOT, ".env"), override=False)

# ---------------------------------------------------------------------------
# Alert configuration — read once at import time, never re-read per alert.
# SMS_CONFIGURED name is kept for compatibility with app.py's existing import
# (it indicates whether Telegram is configured).
# SMS_ALERT_CONFIGURED specifically gates Fast2SMS SMS alerting.
# ---------------------------------------------------------------------------
_TELEGRAM_BOT_TOKEN  = os.environ.get("TELEGRAM_BOT_TOKEN",  "").strip()
_TELEGRAM_CHAT_ID    = os.environ.get("TELEGRAM_CHAT_ID",    "").strip()  # string; may be negative for groups
_FAST2SMS_API_KEY    = os.environ.get("FAST2SMS_API_KEY",    "").strip()
_ALERT_PHONE_NUMBER  = os.environ.get("ALERT_PHONE_NUMBER",  "").strip()  # 10-digit Indian number

# True only if every required key for that service is present and non-empty.
SMS_CONFIGURED: bool = all([_TELEGRAM_BOT_TOKEN, _TELEGRAM_CHAT_ID])
SMS_ALERT_CONFIGURED: bool = all([_FAST2SMS_API_KEY, _ALERT_PHONE_NUMBER])

# Report startup status — presence only, never credential values.
if SMS_CONFIGURED:
    print("[notifier] Telegram alerting ACTIVE "
          "(TELEGRAM_BOT_TOKEN=PRESENT, TELEGRAM_CHAT_ID=PRESENT)")
else:
    _missing_tg = [k for k, v in {
        "TELEGRAM_BOT_TOKEN": _TELEGRAM_BOT_TOKEN,
        "TELEGRAM_CHAT_ID":   _TELEGRAM_CHAT_ID,
    }.items() if not v]
    print(f"[notifier] Telegram alerting DISABLED — missing or empty keys: {_missing_tg}")

if SMS_ALERT_CONFIGURED:
    print("[notifier] Fast2SMS alerting ACTIVE "
          "(FAST2SMS_API_KEY=PRESENT, ALERT_PHONE_NUMBER=PRESENT)")
else:
    _missing_sms = [k for k, v in {
        "FAST2SMS_API_KEY":   _FAST2SMS_API_KEY,
        "ALERT_PHONE_NUMBER": _ALERT_PHONE_NUMBER,
    }.items() if not v]
    print(f"[notifier] Fast2SMS alerting DISABLED — missing or empty keys: {_missing_sms}")




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
# send_sms_alert — Telegram Bot dispatch for confirmed High transitions
# (name kept as send_sms_alert for compatibility with log_alert's call site)
# ---------------------------------------------------------------------------

def send_sms_alert(event: dict) -> None:
    """
    Send a Telegram Bot message for a confirmed High-severity transition.

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

        # Telegram Bot API — sendMessage endpoint.
        # Using POST with a JSON body; chat_id is kept as a string because
        # group/supergroup IDs are negative integers and we never need to
        # do arithmetic on them.
        url = f"https://api.telegram.org/bot{_TELEGRAM_BOT_TOKEN}/sendMessage"
        response = _requests.post(
            url,
            json={
                "chat_id": _TELEGRAM_CHAT_ID,
                "text":    body,
            },
            timeout=10,   # seconds — prevents a slow API call from stalling the stream
        )

        # Telegram always returns 200 for well-formed requests; ok/failure
        # is signalled by the "ok" field in the JSON body.
        result = response.json()
        if result.get("ok") is True:
            message_id = result.get("result", {}).get("message_id", "n/a")
            print(f"[notifier] Telegram message sent successfully (message_id: {message_id})")
        else:
            reason = result.get("description", repr(result))
            print(f"[notifier] Telegram message failed: {reason}")

    except Exception as exc:
        # Generic failure (network error, JSON parse error, etc.).
        # Never raise — caller must remain unaffected by notification issues.
        print(f"[notifier] Telegram alert failed: {type(exc).__name__} — {str(exc)[:120]}")


# ---------------------------------------------------------------------------
# send_fast2sms_alert — Fast2SMS SMS dispatch for confirmed High transitions
# ---------------------------------------------------------------------------

def send_fast2sms_alert(event: dict) -> None:
    """
    Send a Fast2SMS Quick SMS for a confirmed High-severity transition.

    No-ops immediately if SMS_ALERT_CONFIGURED is False (missing .env keys).
    Must never raise — any exception is caught and printed here so that
    the caller (log_alert) is never disrupted.

    Parameters
    ----------
    event : dict
        The 5-field detection contract dict:
        {"timestamp", "class", "confidence", "bbox", "severity"}
    """
    if not SMS_ALERT_CONFIGURED:
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

        # Fast2SMS Quick SMS route — params passed as a dict, not query-string
        # concatenation, so special characters in `body` are safely encoded.
        response = _requests.get(
            "https://www.fast2sms.com/dev/bulkV2",
            params={
                "authorization": _FAST2SMS_API_KEY,
                "route":         "q",
                "message":       body,
                "language":      "english",
                "flash":         "0",
                "numbers":       _ALERT_PHONE_NUMBER,
            },
            timeout=10,   # seconds — prevents a slow API call from stalling the stream
        )

        # Fast2SMS always returns 200; success/failure is in the JSON body.
        result = response.json()
        if result.get("return") is True:
            request_id = result.get("request_id", "n/a")
            print(f"[notifier] Fast2SMS sent successfully (request_id: {request_id})")
        else:
            reason = result.get("message", repr(result))
            print(f"[notifier] Fast2SMS failed: Fast2SMS returned failure — {reason}")

    except Exception as exc:
        # Generic failure (network error, JSON parse error, etc.).
        # Never raise — caller must remain unaffected by SMS issues.
        print(f"[notifier] Fast2SMS alert failed: {type(exc).__name__} — {str(exc)[:120]}")


# ---------------------------------------------------------------------------
# send_telegram_document — send file via Telegram Bot sendDocument API
# ---------------------------------------------------------------------------

def send_telegram_document(file_path: str, caption: str | None = None) -> None:
    """
    Send a document (e.g. PDF incident report) via Telegram Bot API.

    No-ops immediately if SMS_CONFIGURED (Telegram) is False or file does not exist.
    Uses a 20-second timeout to accommodate file uploads.
    Must never raise — any exception is caught and printed so callers are not disrupted.
    """
    if not SMS_CONFIGURED:
        return

    if not file_path or not os.path.isfile(file_path):
        print(f"[notifier] Telegram document skipped — file not found: {file_path!r}")
        return

    try:
        url = f"https://api.telegram.org/bot{_TELEGRAM_BOT_TOKEN}/sendDocument"
        data = {"chat_id": _TELEGRAM_CHAT_ID}
        if caption:
            data["caption"] = caption

        filename = os.path.basename(file_path)
        with open(file_path, "rb") as f:
            files = {"document": (filename, f, "application/pdf")}
            response = _requests.post(
                url,
                data=data,
                files=files,
                timeout=20,  # 20s timeout for document upload
            )

        result = response.json()
        if result.get("ok") is True:
            message_id = result.get("result", {}).get("message_id", "n/a")
            print(f"[notifier] Telegram document sent successfully (message_id: {message_id})")
        else:
            reason = result.get("description", repr(result))
            print(f"[notifier] Telegram document failed: {reason}")

    except Exception as exc:
        print(f"[notifier] Telegram document upload failed: {type(exc).__name__} — {str(exc)[:120]}")


# ---------------------------------------------------------------------------
# log_alert — console output + multi-channel notification for confirmed High transitions
# ---------------------------------------------------------------------------

def log_alert(event: dict, report_path: str | None = None) -> None:
    """
    Print a formatted alert line to stdout for a confirmed High transition,
    then attempt to dispatch alerts across all configured channels:
      1. Telegram text message via send_sms_alert()
      2. Telegram document attachment via send_telegram_document() if report_path provided
      3. Fast2SMS SMS via send_fast2sms_alert()

    Each alert call is wrapped in its own independent try/except so any
    failure in one channel can never affect another channel or silence the
    console log that was already working.

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

    # Telegram text message dispatch — isolated so it can never affect console log or others.
    try:
        send_sms_alert(event)
    except Exception as exc:
        print(f"[notifier] Unexpected error in send_sms_alert: {exc!r}")

    # Telegram document dispatch — isolated so it cannot affect text alerts or Fast2SMS.
    if report_path:
        try:
            confidence = float(event.get("confidence", 0))
            timestamp  = event.get("timestamp", "unknown")
            caption = (
                f"🔥 FIRE ALERT REPORT - SIH26162\n"
                f"Severity: HIGH | Confidence: {confidence * 100:.0f}%\n"
                f"Time: {timestamp}"
            )
            send_telegram_document(report_path, caption=caption)
        except Exception as exc:
            print(f"[notifier] Unexpected error in send_telegram_document: {exc!r}")

    # Fast2SMS dispatch — isolated so it can never affect console log or Telegram.
    try:
        send_fast2sms_alert(event)
    except Exception as exc:
        print(f"[notifier] Unexpected error in send_fast2sms_alert: {exc!r}")



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
