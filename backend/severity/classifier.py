"""
classifier.py — Per-frame severity classification for fire/smoke detections.

Input  : detections from detect_frame() — list of dicts with keys
         "class" ("fire"|"smoke"), "confidence" (float), "bbox" ([x,y,w,h])
Output : one of three labels  →  "Low" | "Medium" | "High"

Design notes
------------
The raw severity *signal* is derived purely from the geometry and confidence
of the detections in a single frame.  Escalation to "High" additionally
requires that fire (not just smoke) has been present in at least
FIRE_PERSIST_N consecutive recent frames, preventing a single stray
detection from immediately triggering the top level.
"""

from collections import deque


# ---------------------------------------------------------------------------
# Tunable thresholds — adjust these without touching the logic below.
# ---------------------------------------------------------------------------

# Confidence floor: detections below this are ignored when building the score.
CONF_FLOOR: float = 0.45

# Fire-specific floor used for the persistence counter (can be stricter).
FIRE_PERSIST_CONF: float = 0.50

# Number of consecutive recent frames that must contain a qualifying fire
# detection before the severity is allowed to reach "High".
FIRE_PERSIST_N: int = 5

# How many recent frames to remember (caps memory usage).
HISTORY_SIZE: int = 15

# Weight of box-area fraction vs. confidence when blending into one score.
#   score = WEIGHT_AREA * area_fraction + WEIGHT_CONF * confidence
# Both weights should sum to 1.0 for an intuitive 0–1 output range.
WEIGHT_AREA: float = 0.5
WEIGHT_CONF: float = 0.5

# Score thresholds that separate the three severity bands.
#   score <  THRESH_LOW              →  "Low"
#   THRESH_LOW  ≤ score < THRESH_HIGH →  "Medium"
#   score ≥ THRESH_HIGH + persistence →  "High"
THRESH_LOW: float = 0.20
THRESH_HIGH: float = 0.50


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _detection_score(det: dict, frame_area: float) -> float:
    """
    Compute a single [0, 1] signal for one detection.

    Area fraction is clamped to [0, 1] so an oversized bbox can't push the
    score above 1.
    """
    _, _, w, h = det["bbox"]
    area_fraction = min((w * h) / frame_area, 1.0) if frame_area > 0 else 0.0
    return WEIGHT_AREA * area_fraction + WEIGHT_CONF * det["confidence"]


# ---------------------------------------------------------------------------
# Main classifier
# ---------------------------------------------------------------------------

class SeverityClassifier:
    """
    Stateful per-stream severity classifier.

    Each call to ``classify()`` updates internal history and returns the
    severity label for the *current* frame.

    Parameters
    ----------
    history_size : int
        Number of past frames to remember.  Defaults to HISTORY_SIZE.
    """

    def __init__(self, history_size: int = HISTORY_SIZE) -> None:
        # Each entry is True/False: did that frame have a qualifying fire det?
        self._fire_streak: deque[bool] = deque(maxlen=history_size)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def classify(
        self,
        detections: list[dict],
        frame_width: int,
        frame_height: int,
    ) -> str:
        """
        Classify the severity for one frame.

        Parameters
        ----------
        detections : list[dict]
            Output of detect_frame() — may be empty.
        frame_width : int
            Width of the source frame in pixels.
        frame_height : int
            Height of the source frame in pixels.

        Returns
        -------
        str
            One of "Low", "Medium", or "High".
        """
        frame_area = frame_width * frame_height

        # --- Step 1: Filter detections by confidence floor ------------------
        valid = [d for d in detections if d["confidence"] >= CONF_FLOOR]

        if not valid:
            # No evidence this frame → record absence and return Low.
            self._fire_streak.append(False)
            return "Low"

        # --- Step 2: Compute per-frame score (max over all valid detections) -
        # Taking the max means we care about the *worst* thing in the frame,
        # not a diluted average across many small boxes.
        score = max(_detection_score(d, frame_area) for d in valid)

        # --- Step 3: Update fire-persistence history ------------------------
        has_fire = any(
            d["class"] == "fire" and d["confidence"] >= FIRE_PERSIST_CONF
            for d in valid
        )
        self._fire_streak.append(has_fire)

        # --- Step 4: Map score + persistence to a label ---------------------
        if score < THRESH_LOW:
            return "Low"

        if score < THRESH_HIGH:
            return "Medium"

        # Score is High-range — only confirm "High" if fire has been
        # consistently present across the required number of recent frames.
        if self._fire_is_persistent():
            return "High"

        # Score looks severe but persistence hasn't been earned yet.
        return "Medium"

    def reset(self) -> None:
        """Clear all history (e.g. when switching to a new video stream)."""
        self._fire_streak.clear()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _fire_is_persistent(self) -> bool:
        """
        Return True if the most recent FIRE_PERSIST_N frames all contained
        a qualifying fire detection.

        If fewer than FIRE_PERSIST_N frames have been seen yet, requires
        all seen frames to have fire (conservative start-up behaviour).
        """
        recent = list(self._fire_streak)[-FIRE_PERSIST_N:]
        return len(recent) >= FIRE_PERSIST_N and all(recent)


# ---------------------------------------------------------------------------
# Quick sanity-check — run:  python classifier.py
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    clf = SeverityClassifier()
    W, H = 1280, 720  # pretend frame size

    scenarios: list[tuple[str, list[dict]]] = [
        # (label, detections)
        ("empty frame",                  []),
        ("low-conf smoke",               [{"class": "smoke", "confidence": 0.30, "bbox": [10, 10, 50, 50]}]),
        ("small smoke, medium conf",     [{"class": "smoke", "confidence": 0.55, "bbox": [100, 100, 80, 80]}]),
        ("medium fire box",              [{"class": "fire",  "confidence": 0.65, "bbox": [200, 150, 200, 200]}]),
        # Repeat a high-confidence, large fire box FIRE_PERSIST_N + 1 times
        # to earn the persistence budget and see "High" appear.
        *[
            (f"large fire #{i+1}/{FIRE_PERSIST_N + 1}",
             [{"class": "fire", "confidence": 0.92, "bbox": [0, 0, 960, 540]}])
            for i in range(FIRE_PERSIST_N + 1)
        ],
        ("back to empty",                []),
        ("small smoke again",            [{"class": "smoke", "confidence": 0.60, "bbox": [50, 50, 60, 60]}]),
    ]

    print(f"{'Scenario':<42}  Severity")
    print("-" * 55)
    for label, dets in scenarios:
        severity = clf.classify(dets, W, H)
        print(f"{label:<42}  {severity}")
