"""
test_pipeline.py — Temporary end-to-end pipeline smoke-test.

Runs detect_frame() + SeverityClassifier over every frame of a sample video
and prints a summary.  No display window — pure terminal output.

Usage:
    python backend/test_pipeline.py
(run from the project root so relative paths resolve correctly)
"""

import sys
import os
import cv2

# ---------------------------------------------------------------------------
# Resolve imports regardless of where Python is invoked from.
# ---------------------------------------------------------------------------
_ROOT = os.path.dirname(os.path.abspath(__file__))  # backend/
sys.path.insert(0, _ROOT)

from inference.detect import detect_frame                   # noqa: E402
from severity.classifier import SeverityClassifier         # noqa: E402

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
VIDEO_PATH = os.path.join(_ROOT, "..", "sample_videos", "test.mp4")

# ---------------------------------------------------------------------------
# Open video
# ---------------------------------------------------------------------------
cap = cv2.VideoCapture(VIDEO_PATH)
if not cap.isOpened():
    print(f"[ERROR] Cannot open video: {VIDEO_PATH!r}")
    print("Place a test video at sample_videos/test.mp4 and re-run.")
    sys.exit(1)

total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
print(f"[INFO] Opened {VIDEO_PATH!r}  ({total_frames} frames reported by container)")

# ---------------------------------------------------------------------------
# Process frames
# ---------------------------------------------------------------------------
clf = SeverityClassifier()
counts = {"Low": 0, "Medium": 0, "High": 0}
frame_idx = 0

while True:
    ret, frame = cap.read()
    if not ret:
        break

    frame_idx += 1
    h, w = frame.shape[:2]   # frame.shape is (height, width, channels)

    detections = detect_frame(frame)
    severity   = clf.classify(detections, frame_width=w, frame_height=h)

    counts[severity] += 1

    # Only print non-trivial frames to keep output readable.
    if severity in ("Medium", "High"):
        print(f"  Frame {frame_idx:>5}  [{severity:<6}]  detections={detections}")

cap.release()

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
processed = frame_idx
print()
print("=" * 50)
print(f"Frames processed : {processed}")
print(f"  Low    : {counts['Low']:>5}  ({counts['Low']/max(processed,1)*100:.1f}%)")
print(f"  Medium : {counts['Medium']:>5}  ({counts['Medium']/max(processed,1)*100:.1f}%)")
print(f"  High   : {counts['High']:>5}  ({counts['High']/max(processed,1)*100:.1f}%)")
print("=" * 50)
