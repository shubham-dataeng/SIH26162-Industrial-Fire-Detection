"""
Developer B — Flask App (B1→B2→B4→B5→Integration)
===================================================
This is Developer B's Flask application for SIH26162 - AI Detection &
Classification of Industrial Fires.

INTEGRATION (Phase 8):
  The real AI pipeline is Developer A's `run_pipeline(frame)` from
  backend/inference/detect.py.  It returns list[dict] with the 5-field
  contract below.  Integration is wrapped in a startup try/except so the
  app still boots and runs on mock data if ultralytics/weights are absent.

  The normalisation layer `get_frame_detections(frame)` is the single call
  site for either the real pipeline or the mock fallback.  generate_frames()
  calls it once per frame and writes the result into `_latest_state` so that
  GET /latest_detection reads EXACTLY what the video stream is processing —
  no duplicate or inconsistent inference calls.

5-FIELD SHARED CONTRACT (must not change without agreement from both devs):
    {
        "timestamp":  str   (ISO 8601, e.g. "2026-09-13T17:12:06.123456"),
        "class":      str   ("fire" | "smoke" | "none"),
        "confidence": float (0.0 – 1.0),
        "bbox":       list[int]  ([x, y, w, h]; [0,0,0,0] when class is "none"),
        "severity":   str   ("Low" | "Medium" | "High" | "None"),
    }

Routes:
  GET  /                   — Dashboard shell (frontend/index.html)
  GET  /video_feed         — MJPEG stream with real/mock overlays
  GET  /latest_detection   — Single detection JSON from shared _latest_state
  GET  /events             — 20 most recent persisted events
  POST /switch_video       — Hot-swap active video source ({"video": "N.mp4"})
  GET  /snapshots/<fname>  — Serve saved alert snapshot image
"""

import os
import sys
import threading
import time
from datetime import datetime

import cv2
import numpy as np
from flask import Flask, Response, jsonify, request, send_from_directory
from flask_cors import CORS

# ---------------------------------------------------------------------------
# sys.path fix — established in Module B4, reused identically here.
# _BACKEND_DIR (backend/) is inserted so that sibling packages
# `database`, `alerts`, and `inference` all resolve via the same mechanism.
# ---------------------------------------------------------------------------
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from database.db import get_recent_events, insert_event  # noqa: E402
# Reusing the same _BACKEND_DIR sys.path pattern established in B4:
from alerts.notifier import log_alert, maybe_alert  # noqa: E402

# ---------------------------------------------------------------------------
# STEP 1 — Safe import of the real AI pipeline with startup fallback.
# Same sys.path pattern as database/alerts above (backend/ already on path).
# ---------------------------------------------------------------------------
REAL_PIPELINE_AVAILABLE: bool = False
_run_pipeline = None  # holds the callable if import succeeds

try:
    from inference.detect import run_pipeline as _run_pipeline  # noqa: E402
    REAL_PIPELINE_AVAILABLE = True
    print("[app] Real AI pipeline loaded: inference.detect.run_pipeline")
except Exception as _import_err:
    print(f"[app] Real AI pipeline unavailable — running on mock detections only: {_import_err!r}")

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

_FRONTEND_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "frontend")
)

_SAMPLE_VIDEOS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "sample_videos")
)

_SNAPSHOTS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "database", "snapshots")
)
os.makedirs(_SNAPSHOTS_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

# Serve frontend/ directly as static_folder (eliminates duplicate backend/static copy that was silently going stale).
app = Flask(__name__, static_folder=_FRONTEND_DIR, static_url_path="/static")
CORS(app)


# ---------------------------------------------------------------------------
# STEP 3 — Shared per-frame state
# ---------------------------------------------------------------------------

# Protected by _state_lock so generate_frames() (writer, in the /video_feed
# thread) and latest_detection() (reader, in its own HTTP thread) never race.
# This is a SEPARATE lock from notifier.py's _lock — do not conflate them.
_state_lock = threading.Lock()
_latest_state: dict = {"detections": [], "updated_at": None}
# Tracks per-frame inference status (Phase 12): True if real pipeline succeeded on last frame, False if fallen back to mock.
_last_frame_was_real: bool = REAL_PIPELINE_AVAILABLE

# ---------------------------------------------------------------------------
# Shared video capture — lifted out of generate_frames() so POST /switch_video
# can hot-swap the source without restarting the streaming generator.
#
# _cap_lock guards both _cap and _cap_is_file.  generate_frames() acquires it
# briefly per frame (just long enough to call cap.read()); /switch_video holds
# it only during the release + re-open sequence, so contention is minimal.
# ---------------------------------------------------------------------------
_cap_lock = threading.Lock()
_cap: cv2.VideoCapture | None = None        # initialised in generate_frames()
_cap_is_file: bool = True                   # True → loop on EOF; False → webcam



# Severity ordering used by pick_primary_detection() and overall_severity().
_SEVERITY_RANK: dict[str, int] = {"High": 3, "Medium": 2, "Low": 1, "None": 0}


def pick_primary_detection(detections: list) -> dict:
    """
    Choose the single most significant detection from the list to represent
    the current frame in GET /latest_detection.

    Returns the item with the highest severity (High > Medium > Low > None).
    Ties are broken by first occurrence (stable sort behaviour).
    Returns a safe "none"-shaped dict if the list is empty, so the downstream
    consumer always gets a well-formed object without null-checking.
    """
    if not detections:
        return {
            "timestamp": datetime.now().isoformat(),
            "class": "none",
            "confidence": 0.0,
            "bbox": [0, 0, 0, 0],
            "severity": "None",
        }
    # max() with key is stable on equal keys (Python spec), so first-occurrence
    # wins on ties — no explicit index tracking needed.
    return max(detections, key=lambda d: _SEVERITY_RANK.get(d.get("severity", "None"), 0))


def overall_severity(detections: list) -> str:
    """
    Return the highest severity string across all detections in the list.
    Returns "None" for an empty list.
    Used to feed maybe_alert() with a frame-level severity value.
    """
    if not detections:
        return "None"
    return max(
        (d.get("severity", "None") for d in detections),
        key=lambda s: _SEVERITY_RANK.get(s, 0),
    )


# ---------------------------------------------------------------------------
# Mock detection — kept for the fallback path in get_frame_detections().
# mock_detect() itself is unchanged so it continues to serve GET /latest_detection
# correctly during mock-only operation via _latest_state.
# ---------------------------------------------------------------------------

def mock_detect() -> dict:
    """
    Deterministic 8-second cycle mock (unchanged from previous modules).

    Cycle:
      [0, 4)  → class="none"  / severity="None"
      [4, 8)  → class="fire"  / confidence=0.91 / severity="High"

    Returns a single dict matching the 5-field contract.
    """
    phase = time.time() % 8

    if phase < 4:
        return {
            "timestamp": datetime.now().isoformat(),
            "class": "none",
            "confidence": 0.0,
            "bbox": [0, 0, 0, 0],
            "severity": "None",
        }
    else:
        return {
            "timestamp": datetime.now().isoformat(),
            "class": "fire",
            "confidence": 0.91,
            "bbox": [120, 80, 60, 60],
            "severity": "High",
        }


# ---------------------------------------------------------------------------
# STEP 2 — Validation helper
# ---------------------------------------------------------------------------

def is_valid_detection(d: dict) -> bool:
    """
    Validate that a detection dict has all 5 required fields with roughly
    correct types.  Used inside get_frame_detections() to guard against
    a malformed real-pipeline response before it reaches downstream code.

    Does NOT validate value ranges (e.g. confidence ∈ [0,1]) — basic type
    checks are enough for a prototype; the real pipeline already enforces
    these constraints internally.
    """
    try:
        return (
            isinstance(d.get("timestamp"), str) and bool(d["timestamp"])
            and isinstance(d.get("class"), str)
            and isinstance(d.get("confidence"), (int, float))
            and isinstance(d.get("bbox"), (list, tuple)) and len(d["bbox"]) == 4
            and isinstance(d.get("severity"), str)
        )
    except Exception:
        return False


# ---------------------------------------------------------------------------
# STEP 2 — Normalisation layer
# ---------------------------------------------------------------------------

def get_frame_detections(frame) -> list:
    """
    Single call site for per-frame inference.

    If REAL_PIPELINE_AVAILABLE is True:
      - Calls _run_pipeline(frame) (= inference.detect.run_pipeline).
      - run_pipeline() already returns list[dict] with all 5 fields.
      - Validates every item; on any validation failure or exception,
        falls back to the mock-adapted result FOR THIS FRAME ONLY (does
        not flip REAL_PIPELINE_AVAILABLE — one bad frame is not fatal).

    If REAL_PIPELINE_AVAILABLE is False:
      - Calls mock_detect() and adapts its single-dict output to list shape:
          class == "none"  → []      (no detections)
          class != "none"  → [dict]  (one detection)
        This makes mock and real output the same list[dict] everywhere.

    Returns list[dict] — always.  May be empty.
    """
    global _last_frame_was_real
    if REAL_PIPELINE_AVAILABLE and _run_pipeline is not None:
        try:
            results = _run_pipeline(frame)
            # Normalise to list (run_pipeline already returns a list, but be
            # defensive in case a future refactor wraps it in a single dict).
            if isinstance(results, dict):
                results = [results]
            elif not isinstance(results, list):
                results = list(results)

            # Validate every item.  Discard malformed ones with a warning.
            valid = []
            for item in results:
                if is_valid_detection(item):
                    valid.append(item)
                else:
                    print(
                        f"[get_frame_detections] Discarding malformed detection "
                        f"from real pipeline: {item!r}"
                    )
            with _state_lock:
                _last_frame_was_real = True
            return valid

        except Exception as pipeline_exc:
            # Per-frame failure — print but do NOT disable the pipeline globally.
            print(
                f"[get_frame_detections] Real pipeline raised on this frame "
                f"(falling back to mock for this frame): {pipeline_exc!r}"
            )
            # Fall through to mock-adapted result for this frame only.

    # Mock-adapted path: convert single dict → list shape.
    with _state_lock:
        _last_frame_was_real = False
    mock = mock_detect()
    if mock.get("class") == "none":
        return []
    return [mock]



# ---------------------------------------------------------------------------
# Video source detection (Module B2 — unchanged)
# ---------------------------------------------------------------------------

_SEVERITY_COLORS = {
    "Low":    (0, 255,   0),   # green  (BGR)
    "Medium": (0, 165, 255),   # orange
    "High":   (0,   0, 255),   # red
}
_DEFAULT_OVERLAY_COLOR = (0, 165, 255)


def get_video_source():
    """
    Locate a sample video file or fall back to the system webcam.
    Returns first alphabetically-sorted .mp4/.avi/.mov path, or int 0.
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

    print("No sample video found in sample_videos/ — falling back to webcam index 0")
    return 0


# ---------------------------------------------------------------------------
# MJPEG frame generator (Module B2, rewired per Steps 3–4)
# ---------------------------------------------------------------------------

def _make_error_frame(message: str) -> bytes:
    """Black 640×480 JPEG with centred red error text."""
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
    MJPEG generator — rewired for Phase 8 Integration.

    Per-frame flow:
      1. Read OpenCV frame from video source.
      2. Call get_frame_detections(frame) ONCE — real pipeline or mock.
      3. Write result into _latest_state under _state_lock so
         GET /latest_detection always reflects what the stream is seeing.
      4. For each detection in the list:
           a. insert_event() — DB persistence (same safety pattern as B4).
           b. Draw bbox + label overlay on the frame.
      5. Feed overall_severity() → maybe_alert() / log_alert() (same as B5,
         now driven by the real list rather than a single mock dict).
      6. Encode and yield the annotated JPEG.

    All error-handling patterns from B2/B4/B5 are preserved:
    - _cap.release() in finally regardless of how the generator exits.
    - Video file EOF → seek to frame 0 for seamless looping.
    - Webcam read failure → break with log.
    - Per-frame detection/draw block isolated in try/except.
    - insert_event wrapped in its own independent try/except.
    - Notifier wrapped in its own independent try/except.
    - JPEG encode failure → continue (skip frame, no corrupt bytes yielded).

    Source hot-swap (POST /switch_video):
    - _cap and _cap_is_file are module-level, guarded by _cap_lock.
    - This generator initialises _cap once at startup (if not already open).
    - /switch_video releases the old _cap and opens a new one under _cap_lock;
      the next cap.read() call in this loop picks up the new source seamlessly.
    """
    global _cap, _cap_is_file

    # Initialise the shared capture on first client connection.
    with _cap_lock:
        if _cap is None or not _cap.isOpened():
            source = get_video_source()
            _cap_is_file = isinstance(source, str)
            _cap = cv2.VideoCapture(source)

        if not _cap.isOpened():
            print(f"[generate_frames] cv2.VideoCapture could not open initial source")
            error_bytes = _make_error_frame("NO VIDEO SOURCE AVAILABLE")
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n"
                + error_bytes
                + b"\r\n"
            )
            return

    try:
        while True:
            # ----------------------------------------------------------------
            # Read one frame under _cap_lock — /switch_video may swap _cap
            # between iterations; we always read from whatever is current.
            # ----------------------------------------------------------------
            with _cap_lock:
                ret, frame = _cap.read()
                is_file = _cap_is_file
                if not ret and is_file:
                    # EOF — loop the file; do this inside the lock so the
                    # seek and read happen atomically with respect to the lock.
                    _cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    ret, frame = _cap.read()

            # ----------------------------------------------------------------
            # Handle read failure
            # ----------------------------------------------------------------
            if not ret:
                if is_file:
                    # Shouldn't reach here (already tried above), but be safe.
                    continue
                else:
                    print("[generate_frames] Webcam read failed — stopping stream.")
                    break

            # ----------------------------------------------------------------
            # STEP 2/4 — One inference call, feeds _latest_state and overlays
            # ----------------------------------------------------------------
            try:
                # One call per frame — no separate call from /latest_detection.
                detections = get_frame_detections(frame)

                # ---- STEP 3: Write shared state under lock -----------------
                with _state_lock:
                    _latest_state["detections"] = detections
                    _latest_state["updated_at"] = datetime.now().isoformat()

                # ---- Draw overlay for all valid detections first -----------
                for det in detections:
                    det_class  = det.get("class", "none")
                    severity   = det.get("severity", "None")
                    confidence = det.get("confidence", 0.0)
                    bbox       = det.get("bbox", [0, 0, 0, 0])

                    if det_class != "none":
                        x, y, w, h = int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
                        color = _SEVERITY_COLORS.get(severity, _DEFAULT_OVERLAY_COLOR)

                        cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)

                        label = f"{det_class} | {severity} | {confidence:.2f}"
                        label_y = max(y - 10, 20)
                        cv2.putText(
                            frame, label,
                            (x, label_y),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.7, color, 2,
                        )

                # ---- STEP 4/5: Alert notifier & snapshot capture -----------
                # maybe_alert() needs the worst severity across ALL detections
                # so it correctly sees "None" on empty frames (latch reset).
                # Independent try/except — a notifier failure cannot affect
                # DB logging or the video stream (B5 pattern preserved).
                snapshot_filename: str | None = None
                primary_det = None

                try:
                    frame_severity = overall_severity(detections)
                    if maybe_alert(frame_severity):
                        primary_det = pick_primary_detection(detections)

                        # Capture and save annotated frame snapshot
                        try:
                            ts_safe = datetime.now().isoformat().replace(":", "-")
                            snapshot_filename = f"snapshot_{ts_safe}.jpg"
                            snapshot_full_path = os.path.join(_SNAPSHOTS_DIR, snapshot_filename)
                            cv2.imwrite(snapshot_full_path, frame)
                        except Exception as snap_exc:
                            print(
                                f"[generate_frames] Snapshot save failed "
                                f"(alert continues): {snap_exc!r}"
                            )
                            snapshot_filename = None

                        log_alert(primary_det)
                except Exception as alert_exc:
                    print(
                        f"[generate_frames] Notifier raised unexpectedly "
                        f"(stream continues): {alert_exc!r}"
                    )

                # ---- DB persistence (B4 pattern) ---------------------------
                # For each detection, persist to DB. If a snapshot was captured
                # on this High-severity transition, associate snapshot_filename
                # ONLY with the primary detection (other detections get None).
                for det in detections:
                    try:
                        det_snap = snapshot_filename if (snapshot_filename and det is primary_det) else None
                        insert_event(det, snapshot_path=det_snap)
                    except Exception as db_exc:
                        print(
                            f"[generate_frames] insert_event raised unexpectedly "
                            f"(stream continues): {db_exc!r}"
                        )

            except Exception as frame_exc:
                # Outer guard: any unhandled error in the detection/draw block
                # passes the clean (un-annotated) frame through to the encoder.
                print(
                    f"[generate_frames] Frame processing error (frame passed clean): "
                    f"{frame_exc!r}"
                )

            # ----------------------------------------------------------------
            # JPEG encode and yield
            # ----------------------------------------------------------------
            ok, buf = cv2.imencode(".jpg", frame)
            if not ok:
                continue

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n"
                + buf.tobytes()
                + b"\r\n"
            )

            # ~30 fps cap
            time.sleep(0.03)

    finally:
        # Do NOT release _cap here — /switch_video may still be using it,
        # and other clients could reconnect.  The cap lives for the app's
        # lifetime; explicit teardown would require an atexit handler.
        pass




# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.route("/")
def index():
    """
    Serve the dashboard shell (frontend/index.html).
    send_from_directory used over render_template — file is static, not a
    Jinja2 template; avoids template engine dependency for a future bundle.
    """
    return send_from_directory(_FRONTEND_DIR, "index.html")


@app.route("/video_feed")
def video_feed():
    """
    MJPEG streaming route.
    generate_frames() handles capture lifecycle, real/mock inference,
    overlay drawing, DB logging, and alert notification internally.
    """
    return Response(
        generate_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/latest_detection")
def latest_detection():
    """
    STEP 5 — Return the most recent detection as JSON.

    Reads _latest_state under _state_lock (written by generate_frames()).
    Passes the detection list through pick_primary_detection() to produce a
    single dict matching the EXACT 5-field shape frontend/script.js expects:
      {"timestamp", "class", "confidence", "bbox", "severity"}

    No inference call is made here — avoids duplicate / inconsistent results
    vs. the video stream that is already running run_pipeline() every frame.

    Falls back to a safe "none" detection on any read or serialisation error.
    """
    try:
        with _state_lock:
            detections = list(_latest_state["detections"])  # shallow copy under lock
            pipeline_mode = "real" if (REAL_PIPELINE_AVAILABLE and _last_frame_was_real) else "mock"
        result = pick_primary_detection(detections)

        # Phase 12: Additive field indicating active pipeline mode ("real" vs "mock").
        # Note: This is an additive field only; existing frontend contracts remain untouched, so this addition cannot break working behavior.
        result["pipeline_mode"] = pipeline_mode
    except Exception as exc:
        print(f"[latest_detection] Error reading shared state: {exc!r}")
        result = {
            "timestamp": datetime.now().isoformat(),
            "class": "none",
            "confidence": 0.0,
            "bbox": [0, 0, 0, 0],
            "severity": "None",
            "pipeline_mode": "mock",
        }
    return jsonify(result)



# Exact set of filenames the switcher will accept — nothing else passes.
_ALLOWED_VIDEOS = {"1.mp4", "2.mp4", "3.mp4"}


@app.route("/switch_video", methods=["POST"])
def switch_video():
    """
    Hot-swap the active MJPEG video source.

    Request body (JSON):
        {"video": "1.mp4"}   # must be one of _ALLOWED_VIDEOS

    Security: only _ALLOWED_VIDEOS filenames are accepted — no path
    separators, no arbitrary filenames.  os.path.basename is applied as a
    belt-and-suspenders guard even though the allowlist already prevents
    traversal.

    Thread safety: acquires _cap_lock to atomically release the old capture
    and open the new one.  generate_frames() holds _cap_lock only for the
    duration of a single cap.read() call, so this blocks for at most one
    frame (~33 ms at 30 fps).

    Response:
        200  {"status": "ok",    "video": "<filename>"}
        400  {"status": "error", "message": "<reason>"}
        500  {"status": "error", "message": "<reason>"}
    """
    global _cap, _cap_is_file

    data = request.get_json(silent=True)
    if not data or "video" not in data:
        return jsonify({"status": "error", "message": "JSON body with 'video' key required"}), 400

    # Belt-and-suspenders: strip any directory component before allowlist check.
    requested = os.path.basename(str(data["video"]))

    if requested not in _ALLOWED_VIDEOS:
        return jsonify({
            "status": "error",
            "message": f"Invalid video '{requested}'. Must be one of: {sorted(_ALLOWED_VIDEOS)}",
        }), 400

    new_path = os.path.join(_SAMPLE_VIDEOS_DIR, requested)
    if not os.path.isfile(new_path):
        return jsonify({
            "status": "error",
            "message": f"Video file not found on server: {requested}",
        }), 400

    try:
        with _cap_lock:
            # Release the old capture before opening the new one to free
            # the file descriptor promptly (important on some OS/drivers).
            if _cap is not None:
                _cap.release()

            _cap = cv2.VideoCapture(new_path)
            _cap_is_file = True  # all allowed videos are files, not webcams

            if not _cap.isOpened():
                return jsonify({
                    "status": "error",
                    "message": f"cv2.VideoCapture failed to open: {requested}",
                }), 500

        print(f"[switch_video] Source switched to: {new_path!r}")
        return jsonify({"status": "ok", "video": requested}), 200

    except Exception as exc:
        print(f"[switch_video] Unexpected error: {exc!r}")
        return jsonify({"status": "error", "message": "Internal error during source switch"}), 500


@app.route("/events")
def events():
    """
    Return the 20 most recent persisted detection events as JSON (Module B4).
    Falls back to [] on any failure — a read error must never 500 the dashboard.
    """
    try:
        result = get_recent_events(20)
    except Exception as exc:
        print(f"[events] get_recent_events raised unexpectedly: {exc!r}")
        result = []
    return jsonify(result)


@app.route("/snapshots/<filename>")
def get_snapshot(filename: str):
    """
    Serve saved snapshot images from backend/database/snapshots/.

    Security: uses os.path.basename to strip any path traversal sequences
    (../, absolute paths) before passing to send_from_directory.
    """
    safe_filename = os.path.basename(filename)
    file_path = os.path.join(_SNAPSHOTS_DIR, safe_filename)
    if not os.path.isfile(file_path):
        return jsonify({"error": "Snapshot not found"}), 404
    return send_from_directory(_SNAPSHOTS_DIR, safe_filename)



# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Supports both:
    #   python backend/app.py                (direct execution)
    #   flask --app backend/app run --debug  (Flask CLI)
    app.run(debug=True)
