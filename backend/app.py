"""
Developer B — Flask App Skeleton (Module B1)
=============================================
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
  GET /video_feed         — Placeholder; MJPEG streaming implemented in next module
  GET /latest_detection   — Returns the most recent detection as JSON
"""

import os
import time
from datetime import datetime

from flask import Flask, Response, jsonify, send_from_directory
from flask_cors import CORS

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = Flask(__name__)

# Enable CORS globally so the dashboard (served on a different port during
# development, or via fetch() in the browser) can poll /latest_detection freely.
CORS(app)

# ---------------------------------------------------------------------------
# Mock detection — TEMPORARY, replaces Developer A's run_pipeline() later
# ---------------------------------------------------------------------------

# Absolute path to the frontend/ directory, resolved relative to this file's
# location so it works regardless of the working directory from which Flask is
# launched (e.g., `flask --app backend/app run` from repo root, or
# `python backend/app.py` directly).
_FRONTEND_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "frontend")
)


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
    Placeholder route — real MJPEG streaming with OpenCV will be implemented
    in the next module.  Returning HTTP 200 with plain text so existing tests
    and pollers don't receive a 404 or 501.
    """
    return Response(
        "video_feed placeholder — implemented in next module",
        status=200,
        mimetype="text/plain",
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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Supports both:
    #   python backend/app.py                (direct execution)
    #   flask --app backend/app run --debug  (Flask CLI)
    app.run(debug=True)
