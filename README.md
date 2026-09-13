# SIH26162 — AI Detection & Classification of Industrial Fires

A real-time AI-powered fire and smoke detection prototype built for industrial monitoring. The system captures live video feeds (webcam or video file), executes computer vision inference (YOLOv8) to identify flame and smoke regions, scores incident severity using confidence and temporal persistence heuristics, persists detection events to an SQLite database, and presents live streaming video, overlays, and alerts on a dark-themed industrial browser dashboard. 

> **Disclaimer:** This software is a hackathon prototype developed for evaluation and proof-of-concept demonstration purposes. It is **not** a certified industrial safety, fire-suppression, or life-safety system.

---

## Setup Instructions

Follow these steps to set up the environment and run the project from a fresh git clone.

### Prerequisites
* Python 3.10 or newer
* Git
* Linux environment (or macOS/WSL2)

### Step 1: Clone and Create Virtual Environment
```bash
git clone https://github.com/shubham-dataeng/SIH26162-Industrial-Fire-Detection.git
cd SIH26162-Industrial-Fire-Detection

# Create a local virtual environment
python3 -m venv venv

# Activate the virtual environment
source venv/bin/activate
```

### Step 2: Install Dependencies
Install all required Python packages:
```bash
pip install -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cpu
```
*Core libraries installed include: `Flask==3.1.3`, `flask-cors==6.0.5`, `opencv-python==5.0.0.93`, `ultralytics==8.4.150`, `torch==2.14.0+cpu`, `torchvision==0.29.0+cpu`, and `huggingface-hub==1.31.0`.*

### Step 3: Download Model Weights (MANDATORY)
The YOLOv8 model weights file (`fire_yolo.pt`) is excluded from git tracking (`*.pt` in `.gitignore`). You **must** download the weights file before starting the application, or the server will operate in fallback/mock mode.

Run Developer A's download script:
```bash
cd backend/inference
python download_model.py
cd ../..
```
*This downloads `best.pt` from Hugging Face Hub (`rabahdev/fire-smoke-yolov8n`) and places it at `backend/inference/model_weights/fire_yolo.pt` (6.0 MB).*

---

## How to Run the Demo

Ensure your virtual environment is activated (`source venv/bin/activate`).

### Method 1: Quick Start with Recovery Script (Recommended)
A demo launcher and recovery script is provided at the repository root:
```bash
./run_demo.sh
```
What `run_demo.sh` does automatically:
1. Frees port `5000` by terminating any stale listening processes (`fuser -k 5000/tcp`).
2. Validates that `./venv` exists and activates it.
3. Launches the Flask app in the foreground on `http://127.0.0.1:5000`.

### Method 2: Manual Launch
Run Flask directly using the CLI:
```bash
flask --app backend/app run
```
Or execute the application script:
```bash
python backend/app.py
```

### Accessing the Dashboard
Open your web browser and navigate to:
```
http://127.0.0.1:5000
```
* **Video Source:** The system automatically checks `sample_videos/` for `.mp4` test clips (defaults to `sample_videos/cooking_fire.mp4`). If no video file is present, it falls back to webcam index `0`.

---

## Folder Structure

Below is the repository structure reflecting current implementation and team ownership:

```text
SIH26162-Industrial-Fire-Detection/
├── backend/
│   ├── alerts/
│   │   ├── __init__.py
│   │   └── notifier.py         # Thread-safe severity transition & alert logger      [Dev B]
│   ├── app.py                  # Flask server, routes, streaming, shared state       [Dev B]
│   ├── database/
│   │   ├── __init__.py
│   │   └── db.py               # SQLite helper functions (events table)              [Dev B]
│   ├── inference/
│   │   ├── detect.py           # YOLOv8 frame inference & run_pipeline() entrypoint [Dev A]
│   │   ├── download_model.py   # Script to fetch weights from Hugging Face           [Dev A]
│   │   ├── model_weights/      # Directory for fire_yolo.pt (created at runtime)     [Dev A]
│   │   ├── sanity_check.py     # Quick validation script on still images             [Dev A]
│   │   └── test_images/        # Sample still images (fire, smoke, none)             [Dev A]
│   ├── severity/
│   │   └── classifier.py       # Rule-based severity scoring with persistence logic  [Dev A]
│   ├── test_pipeline.py        # Video pipeline test runner                          [Dev A]
│   └── A5_RESULTS.md           # Model accuracy validation report                    [Dev A]
├── frontend/
│   ├── index.html              # Single-page dashboard UI shell                      [Dev B]
│   ├── script.js               # Client polling, Audio API beep, event list rendering[Dev B]
│   └── style.css               # Dark industrial dashboard styles & alert animations [Dev B]
├── sample_videos/              # Shared benchmark video clips                        [Shared]
│   ├── cooking_fire.mp4        # Localized flame & smoke test clip
│   ├── sunset_timelapse.mp4    # Clean sky non-fire negative test clip
│   ├── test.mp4                # Raging fire positive escalation test clip
│   └── traffic_sunset.mp4      # Glare & headlight non-fire negative test clip
├── .gitignore                  # Excludes venv/, *.pt, *.db, __pycache__/
├── PROJECT_STATE.md            # Team decisions and project status
├── requirements.txt            # Frozen dependency versions
├── run_demo.sh                 # One-command demo recovery and launch script         [Dev B]
└── README.md                   # Project documentation
```

* **Developer A Ownership:** `backend/inference/` (model inference & weights), `backend/severity/` (rule-based severity classifier).
* **Developer B Ownership:** `backend/app.py` (Flask server & streaming), `backend/database/` (SQLite logging), `backend/alerts/` (notification logic), `frontend/` (dashboard UI), `run_demo.sh` (demo recovery script).
* **Shared Assets:** `sample_videos/`, `requirements.txt`, `README.md`.

---

## API Routes Reference

| Route | Method | Content-Type | Description |
|---|---|---|---|
| `/` | `GET` | `text/html` | Serves the single-page monitoring dashboard (`frontend/index.html`). |
| `/video_feed` | `GET` | `multipart/x-mixed-replace` | Live MJPEG video stream with real-time bounding box overlays, labels, and color-coded severity boxes drawn on each frame. |
| `/latest_detection` | `GET` | `application/json` | Returns the primary detection for the current frame from thread-safe shared state. Includes `pipeline_mode` (`"real"` when YOLO inference is active; `"mock"` in fallback mode). |
| `/events` | `GET` | `application/json` | Returns an array of the 20 most recent persistent detection events from the SQLite database (`events.db`), ordered newest first. |

### Sample Response: `GET /latest_detection`
```json
{
  "bbox": [1058, 822, 130, 159],
  "class": "fire",
  "confidence": 0.914,
  "severity": "High",
  "timestamp": "2026-09-13T19:05:23.405248",
  "pipeline_mode": "real"
}
```

---

## Known Limitations

1. **Prototype-Grade Accuracy:** Detection accuracy relies on YOLOv8n pretrained features and camera viewpoint. False positives can arise from intense glare or flame-like reflections, though persistence filtering minimizes sporadic triggers.
2. **Simulated Sensor Inputs:** No physical heat, gas, or thermocouple sensors are wired in; sensor telemetry and fire-type classifications (electrical, chemical, general) operate on heuristic approximations.
3. **Model Weights Gitignored:** As `.pt` model files are large binary artifacts, `fire_yolo.pt` is not tracked in the git repository. It must be fetched via `python backend/inference/download_model.py` during setup.
4. **Fallback Mode Indicator:** If model weights are missing or inference encounters an unrecoverable exception, `backend/app.py` catches the error and falls back to mock detection without terminating the video stream. When this happens, the dashboard displays a prominent amber banner:
   ```text
   ⚠ Running on fallback detection (AI module offline)
   ```
   Demo presenters should verify this indicator is hidden (confirming `pipeline_mode: "real"`).
5. **Inference Latency:** On local development CPU hardware, real YOLOv8 inference averages **~25–56 ms** per frame (~18–30 FPS), well within the hackathon's `< 200 ms` budget. Actual latency will vary depending on CPU/GPU hardware.

---

## Credits & Team

Built for the **Smart India Hackathon (SIH26162)** by a two-developer team:
* **Developer A:** AI/Inference Pipeline, YOLOv8 Model Sourcing, Dataset Curation, and Severity Classification (`backend/inference/`, `backend/severity/`).
* **Developer B:** Backend Architecture, Video Streaming Engine, SQLite Event Persistence, Alert Notifier, Frontend Dashboard, and System Integration (`backend/app.py`, `backend/database/`, `backend/alerts/`, `frontend/`, `run_demo.sh`).
