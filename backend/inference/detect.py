"""
detect.py — Fire/smoke inference using YOLOv8.

Model: backend/inference/model_weights/fire_yolo.pt
       (downloaded from rabahdev/fire-smoke-yolov8n via download_model.py)

Class mapping (from the training repo):
  0 → smoke
  1 → fire
"""

import os
import cv2
from ultralytics import YOLO

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


# ---------------------------------------------------------------------------
# Quick smoke-test: feed a local video file through the detector and print
# any detections to the terminal. Press 'q' to quit early.
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

    print(f"[INFO] Running detection on {VIDEO_PATH!r} — press 'q' to quit.")

    while True:
        ret, frame = cap.read()
        if not ret:
            # End of video (or a read error).
            print("[INFO] Video ended.")
            break

        found = detect_frame(frame)
        if found:
            print(found)

        # Show the frame so the user can see what's being processed.
        # cv2.imshow("Fire/Smoke Detection", frame)
        # if cv2.waitKey(1) & 0xFF == ord("q"):
        #     print("[INFO] Quit requested.")
        #     break

    cap.release()
    cv2.destroyAllWindows()
