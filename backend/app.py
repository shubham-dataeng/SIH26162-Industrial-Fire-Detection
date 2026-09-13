"""
Developer B — Flask App Skeleton (Module B1 → updated B2 → updated B4)
=======================================================================
This is Developer B's skeleton Flask application for SIH26162 - AI Detection &
Classification of Industrial Fires.

INTEGRATION NOTE:
  The function `mock_detect()` below is a *temporary stand-in* for Developer A's
  real inference pipeline.  During the Integration phase, `mock_detect()` will be
  replaced by a call to Developer A's `run_pipeline()` (from backend/inference/).
  The 5-field response contract returned by this function MUST NOT change without
  explicit agreement from both developers:

      {
          "timestamp":  str   (ISO 8601, e.g. "2026-09-13T17:12:06.123456"),
          "class":      str   ("fire" | "smoke" | "none"),
          "confidence": float (0.0 – 1.0),
          "bbox":       list[int]  ([x, y, w, h]; [0,0,0,0] when class is "none"),
          "severity":   str   ("Low" | "Medium" | "High" | "None"),
      }

Routes defined here:
  GET /                   — Serves the React/HTML dashboard shell (frontend/index.html)
  GET /video_feed         — MJPEG stream with mock-detection overlays (Module B2)
  GET /latest_detection   — Returns the most recent detection as JSON
  GET /events             — Returns the 20 most recent persisted detection events (Module B4)
"""

import os
import sys
import time
from datetime import datetime

import cv2
import numpy as np
from flask import Flask, Response, jsonify, send_from_directory
from flask_cors import CORS

# ---------------------------------------------------------------------------
# Import path fix for backend/database/db.py (Module B4)
# ---------------------------------------------------------------------------
# When this app is launched as `flask --app backend/app run` from the repo
# root, Flask adds the repo root to sys.path.  `from database.db import ...`
# would then look for a top-level `database` package — which does not exist
# at the repo root; it lives inside `backend/`.
#
# When launched as `python backend/app.py`, Python adds the script's own
# directory (backend/) to sys.path[0], so `from database.db import ...` DOES
# resolve correctly — but only in that case.
#
# The robust solution: unconditionally ensure the backend/ directory is in
# sys.path before the import, using __file__ (always points to backend/app.py
# regardless of the CWD or launch method).  The `if ... not in` guard
# prevents duplicate path entries on re-import or interactive reloaders.
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from database.db import get_recent_events, insert_event  # noqa: E402

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = Flask(__name__)

# Enable CORS globally so the dashboard (served on a different port during
# development, or via fetch() in the browser) can poll /latest_detection freely.
CORS(app)

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

# Absolute path to the frontend/ directory, resolved relative to this file's
# location so it works regardless of the working directory from which Flask is
# launched (e.g., `flask --app backend/app run` from repo root, or
# `python backend/app.py` directly).
_FRONTEND_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "frontend")
)

# Absolute path to sample_videos/ at the repo root.
_SAMPLE_VIDEOS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "sample_videos")
)

# ---------------------------------------------------------------------------
# Mock detection — TEMPORARY, replaces Developer A's run_pipeline() later
# ---------------------------------------------------------------------------


def mock_detect() -> dict:
    """
    Simulate Developer A's detection output using a deterministic 8-second cycle.

    Cycle (based on time.time() % 8):
      [0, 4)  -> class="none"  / severity="None"   (quiet period)
      [4, 8)  -> class="fire"  / severity="High"   (fire detected)

    Returns a dict matching the 5-field shared contract exactly.
    This function will be removed and replaced by Developer A's run_pipeline()
    during the Integration phase.
    """
    phase = time.time() % 8  # value in [0, 8)

    if phase < 4:
        # Quiet period — nothing detected
        return {
            "timestamp": datetime.now().isoformat(),
            "class": "none",
            "confidence": 0.0,
            "bbox": [0, 0, 0, 0],
            "severity": "None",
        }
    else:
        # Fire detected — fixed representative values for mock
        return {
            "timestamp": datetime.now().isoformat(),
            "class": "fire",
            "confidence": 0.91,
            "bbox": [120, 80, 60, 60],
            "severity": "High",
        }


# ---------------------------------------------------------------------------
# Video source detection (Module B2)
# ---------------------------------------------------------------------------

# Overlay colour palette (OpenCV uses BGR, not RGB).
_SEVERITY_COLORS = {
    "Low":    (0, 255,   0),   # green
    "Medium": (0, 165, 255),   # orange
    "High":   (0,   0, 255),   # red
}
_DEFAULT_OVERLAY_COLOR = (0, 165, 255)  # orange for unknown/unexpected severity


def get_video_source():
    """
    Locate a sample video file or fall back to the system webcam.

    Checks _SAMPLE_VIDEOS_DIR for files ending in .mp4, .avi, or .mov
    (case-insensitive).  Returns the alphabetically first match as a string
    path, or integer 0 (default webcam index) if none are found.

    Prints its decision to stdout so it is visible in the Flask dev-server log.
    """
    video_extensions = (".mp4", ".avi", ".mov")

    if os.path.isdir(_SAMPLE_VIDEOS_DIR):
        candidates = sorted(
            os.path.join(_SAMPLE_VIDEOS_DIR, f)
            for f in os.listdir(_SAMPLE_VIDEOS_DIR)
            if f.lower().endswith(video_extensions)
        )
        if candidates:
            chosen = candidates[0]
            print(f"Using sample video: {chosen}")
            return chosen

    print(
        "No sample video found in sample_videos/ — "
        "falling back to webcam index 0"
    )
    return 0


# ---------------------------------------------------------------------------
# MJPEG frame generator (Module B2)
# ---------------------------------------------------------------------------


def _make_error_frame(message: str) -> bytes:
    """
    Produce a single black 640x480 JPEG frame with centred red error text.
    Used as the sole yielded frame when no video source is available.
    """
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    font = cv2.FONT_HERSHEY_SIMPLEX
    text_size, _ = cv2.getTextSize(message, font, 0.8, 2)
    text_x = (640 - text_size[0]) // 2
    text_y = (480 + text_size[1]) // 2
    cv2.putText(frame, message, (text_x, text_y), font, 0.8, (0, 0, 255), 2)
    _, buf = cv2.imencode(".jpg", frame)
    return buf.tobytes()


def generate_frames():
    """
    MJPEG generator.  Yields multipart boundary-wrapped JPEG frames for the
    /video_feed route.

    Lifecycle:
    - Opens cv2.VideoCapture once; releases it in a finally block so the
      camera/file handle is always freed, even on GeneratorExit (client
      disconnect) or any unhandled exception.
    - Sample videos loop seamlessly by seeking back to frame 0 on EOF.
    - Webcam: breaks the loop on a failed read (device unavailable).
    - Per-frame detection + overlay errors are caught locally so one bad
      detection dict never kills the stream.
    - Encoding failures skip the frame silently rather than yielding corrupt
      bytes.
    - Sleeps 30 ms per iteration (~30 fps cap) to avoid pinning the CPU.
    """
    source = get_video_source()
    is_file = isinstance(source, str)

    cap = cv2.VideoCapture(source)

    try:
        if not cap.isOpened():
            print(
                f"[generate_frames] cv2.VideoCapture could not open source: {source!r}"
            )
            error_bytes = _make_error_frame("NO VIDEO SOURCE AVAILABLE")
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n"
                + error_bytes
                + b"\r\n"
            )
            return  # end the generator — do not loop

        while True:
            ret, frame = cap.read()

            # ----------------------------------------------------------------
            # Handle read failure
            # ----------------------------------------------------------------
            if not ret:
                if is_file:
                    # Video file ended — loop it back to the beginning for a
                    # seamless continuous demo display.
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                else:
                    # Webcam read failed (device disconnected / unavailable).
                    print(
                        "[generate_frames] Webcam read failed — "
                        "stopping stream."
                    )
                    break

            # ----------------------------------------------------------------
            # Detection overlay
            # ----------------------------------------------------------------
            try:
                detection = mock_detect()
                det_class = detection.get("class", "none")
                severity = detection.get("severity", "None")
                confidence = detection.get("confidence", 0.0)
                bbox = detection.get("bbox", [0, 0, 0, 0])

                # ---- Persist detection event (Module B4) -------------------
                # Insert BEFORE drawing the overlay so logging and rendering
                # are decoupled — a draw failure cannot suppress a log entry.
                # Only log non-"none" detections: empty frames are not events.
                # Belt-and-suspenders try/except wraps insert_event's own
                # internal guard so a logging failure can NEVER interrupt the
                # video stream under any circumstances.
                if det_class != "none":
                    try:
                        insert_event(detection)
                    except Exception as db_exc:
                        print(
                            f"[generate_frames] insert_event raised unexpectedly "
                            f"(stream continues): {db_exc!r}"
                        )

                if det_class != "none":
                    x, y, w, h = int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
                    color = _SEVERITY_COLORS.get(severity, _DEFAULT_OVERLAY_COLOR)

                    # Draw bounding box (bbox is [x,y,w,h] → convert to corners)
                    cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)

                    # Draw label above the box; clamp so it never goes off-screen
                    label = f"{det_class} | {severity} | {confidence:.2f}"
                    label_y = max(y - 10, 20)
                    cv2.putText(
                        frame, label,
                        (x, label_y),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.7, color, 2,
                    )

                # If det_class == "none", pass the clean frame through unchanged.

            except Exception as overlay_exc:
                # One bad detection dict must not kill the stream.
                print(
                    f"[generate_frames] Overlay error (frame passed clean): "
                    f"{overlay_exc!r}"
                )
                # frame is still valid — yield it un-annotated below.

            # ----------------------------------------------------------------
            # JPEG encode and yield
            # ----------------------------------------------------------------
            ok, buf = cv2.imencode(".jpg", frame)
            if not ok:
                # Encoding failure — skip rather than yield corrupt bytes.
                continue

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n"
                + buf.tobytes()
                + b"\r\n"
            )

            # ~30 fps cap — keeps CPU usage reasonable without throttling the UI
            time.sleep(0.03)

    finally:
        # Always release the capture resource, regardless of how the generator
        # exits (normal return, break, GeneratorExit from client disconnect,
        # or any unhandled exception propagating out).
        cap.release()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.route("/")
def index():
    """
    Serve the dashboard shell.

    Using send_from_directory (rather than render_template) because index.html
    is a standalone static file that lives outside Flask's conventional
    'templates/' folder.  send_from_directory avoids introducing a template
    engine dependency for what will eventually be a pre-built frontend bundle.
    """
    return send_from_directory(_FRONTEND_DIR, "index.html")


@app.route("/video_feed")
def video_feed():
    """
    MJPEG streaming route (Module B2).

    Returns a multipart/x-mixed-replace response so browsers display a
    continuous live video feed.  generate_frames() handles source detection,
    OpenCV capture lifecycle, mock-detection overlays, and graceful error
    recovery internally.
    """
    return Response(
        generate_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/latest_detection")
def latest_detection():
    """
    Return the most recent detection result as JSON.

    The try/except lives here in the route (not inside mock_detect) so that
    ANY unexpected error during detection — including future integration errors
    from Developer A's pipeline — is caught at the HTTP boundary and never
    causes a 500 to the client.
    """
    try:
        result = mock_detect()
    except Exception as exc:
        # Print to stdout so it appears in the Flask dev-server log.
        print(f"[latest_detection] mock_detect() raised an exception: {exc!r}")
        # Return a safe "none" detection so the dashboard keeps running.
        result = {
            "timestamp": datetime.now().isoformat(),
            "class": "none",
            "confidence": 0.0,
            "bbox": [0, 0, 0, 0],
            "severity": "None",
        }
    return jsonify(result)


@app.route("/events")
def events():
    """
    Return the 20 most recent persisted detection events as JSON (Module B4).

    Calls get_recent_events() from backend/database/db.py.  On any failure
    (database unavailable, corrupt data, etc.) returns an empty list with
    HTTP 200 — a read failure must never 500 the dashboard.

    Row order: newest first (ORDER BY id DESC in the query).
    """
    try:
        result = get_recent_events(20)
    except Exception as exc:
        # Should never reach here (get_recent_events has its own guard), but
        # belt-and-suspenders at the HTTP boundary.
        print(f"[events] get_recent_events raised unexpectedly: {exc!r}")
        result = []
    return jsonify(result)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Supports both:
    #   python backend/app.py                (direct execution)
    #   flask --app backend/app run --debug  (Flask CLI)
    app.run(debug=True)
