"""
detect.py — Fire/smoke inference using YOLOv8.

Model: backend/inference/model_weights/fire_yolo.pt
       (downloaded from rabahdev/fire-smoke-yolov8n via download_model.py)

Class mapping (from the training repo):
  0 → smoke
  1 → fire

Public API
----------
detect_frame(frame)  → raw detection dicts (class, confidence, bbox)
run_pipeline(frame)  → enriched dicts (+ timestamp, severity) — main entry
                        point for downstream consumers (Developer B).
"""

import os
import sys
from datetime import datetime

import cv2
from ultralytics import YOLO

# ---------------------------------------------------------------------------
# Make `backend/` importable as a package root so that the sibling package
# `severity` can be found via `from severity.classifier import ...`.
# backend/inference/detect.py  →  __file__ parent parent = backend/
# ---------------------------------------------------------------------------
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from severity.classifier import SeverityClassifier  # noqa: E402

# ---------------------------------------------------------------------------
# Model is loaded ONCE at import time so every call to detect_frame() reuses
# the same in-memory weights — critical for real-time video pipelines.
# ---------------------------------------------------------------------------
_MODEL_PATH = os.path.join(os.path.dirname(__file__), "model_weights", "fire_yolo.pt")
_model = YOLO(_MODEL_PATH)

# Maps the integer class index to a human-readable label.
_CLASS_NAMES = {0: "smoke", 1: "fire"}

# Detections below this score are discarded.
_CONFIDENCE_THRESHOLD = 0.45

# ---------------------------------------------------------------------------
# Severity classifier — ONE instance so the persistence window accumulates
# across all frames, exactly like the model being loaded once.
# ---------------------------------------------------------------------------
_classifier = SeverityClassifier()


def detect_frame(frame):
    """
    Run fire/smoke detection on a single BGR frame.

    Parameters
    ----------
    frame : numpy.ndarray
        A single video frame in BGR colour order, as returned by
        cv2.VideoCapture().read().

    Returns
    -------
    list[dict]
        One dict per detection above the confidence threshold, with keys:
          - "class"      (str)   : "fire" or "smoke"
          - "confidence" (float) : score in [0, 1]
          - "bbox"       (list)  : [x, y, w, h] in integer pixel coordinates,
                                   where (x, y) is the top-left corner.
        Returns an empty list when no qualifying detections are found.
    """
    # verbose=False silences per-frame console output from ultralytics.
    results = _model(frame, verbose=False)

    detections = []
    for result in results:
        for box in result.boxes:
            confidence = float(box.conf[0])
            if confidence < _CONFIDENCE_THRESHOLD:
                continue

            class_id = int(box.cls[0])
            class_name = _CLASS_NAMES.get(class_id, "unknown")

            # box.xywh gives [centre_x, centre_y, w, h]; convert to
            # top-left [x, y, w, h] to match a standard bbox convention.
            cx, cy, w, h = box.xywh[0].tolist()
            x = int(cx - w / 2)
            y = int(cy - h / 2)

            detections.append(
                {
                    "class": class_name,
                    "confidence": round(confidence, 4),
                    "bbox": [x, y, int(w), int(h)],
                }
            )

    return detections


def run_pipeline(frame):
    """
    Full pipeline entry point: detection + severity classification.

    Calls detect_frame(), then classifies the severity of the entire frame
    once (not per-detection) and attaches the result — along with an ISO
    timestamp — to every detection dict.

    The classifier is ALWAYS called, even when there are no detections, so
    its internal persistence window advances correctly on every frame.

    Parameters
    ----------
    frame : numpy.ndarray
        A single BGR video frame (from cv2.VideoCapture().read()).

    Returns
    -------
    list[dict]
        Each dict has the shape expected by downstream consumers:
          {
            "timestamp"  : str   — ISO-8601 datetime of this call
            "class"      : str   — "fire" or "smoke"
            "confidence" : float — detection score in [0, 1]
            "bbox"       : list  — [x, y, w, h] in pixels (top-left origin)
            "severity"   : str   — "Low", "Medium", or "High"
          }
        Returns an empty list if there are no qualifying detections,
        while still advancing the classifier's internal state.
    """
    detections = detect_frame(frame)

    # frame.shape is (height, width, channels).
    frame_height, frame_width = frame.shape[:2]

    # Always tick the classifier so the persistence window is consistent,
    # even on frames with no detections (empty list → streak records False).
    severity = _classifier.classify(detections, frame_width, frame_height)

    if not detections:
        return []

    timestamp = datetime.now().isoformat()

    return [
        {
            "timestamp": timestamp,
            "class": det["class"],
            "confidence": det["confidence"],
            "bbox": det["bbox"],
            "severity": severity,   # same label for every detection this frame
        }
        for det in detections
    ]


# ---------------------------------------------------------------------------
# Quick smoke-test: feed a local video file through the full pipeline and
# print any non-empty results to the terminal. Press 'q' to quit early.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    VIDEO_PATH = os.path.join(os.path.dirname(__file__), "../../sample_videos/test.mp4")

    try:
        cap = cv2.VideoCapture(VIDEO_PATH)
        if not cap.isOpened():
            # VideoCapture.isOpened() returns False for missing files too.
            raise FileNotFoundError(f"Could not open video: {VIDEO_PATH!r}")
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}")
        print("Place a test video at sample_videos/test.mp4 and re-run.")
        sys.exit(1)

    print(f"[INFO] Running full pipeline on {VIDEO_PATH!r} — press 'q' to quit.")

    while True:
        ret, frame = cap.read()
        if not ret:
            # End of video (or a read error).
            print("[INFO] Video ended.")
            break

        results = run_pipeline(frame)
        if results:
            print(results)

        # Uncomment to display the video window while testing:
        # cv2.imshow("Fire/Smoke Detection", frame)
        # if cv2.waitKey(1) & 0xFF == ord("q"):
        #     print("[INFO] Quit requested.")
        #     break

    cap.release()
    cv2.destroyAllWindows()
