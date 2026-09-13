/**
 * SIH26162 — Industrial Fire Detection Dashboard
 * script.js  |  Developer B  |  Module B3
 *
 * Polls GET /latest_detection every 1 s, updates:
 *   - Severity badge (every poll — reflects live state)
 *   - Alert banner   (shown/hidden on transition in/out of "High")
 *   - Beep tone      (Web Audio API, triggered ONCE per High transition)
 *   - Detection counter + event list (only on genuinely new detections,
 *     de-duplicated by timestamp to avoid spamming during the 4-second mock
 *     window where the same reading is returned repeatedly)
 */

"use strict";

/* ==========================================================================
   DOM element cache — looked up once at load time, reused in every poll.
   ========================================================================== */
const severityBadge    = document.getElementById("severity-badge");
const pipelineStatus   = document.getElementById("pipeline-status");
const alertBanner      = document.getElementById("alert-banner");
const detectionCounter = document.getElementById("detection-counter");
const eventList        = document.getElementById("event-list");


/* ==========================================================================
   State variables
   ========================================================================== */
let prevSeverity  = null;   // tracks previous poll's severity for transition detection
let lastTimestamp = null;   // tracks the last timestamp we already logged
let sessionCount  = 0;      // running total of distinct non-"none" detections

// Lazily created AudioContext (browsers require a user gesture before audio
// context can run; we create it on first beep attempt and call .resume() to
// handle the suspended-autoplay policy gracefully).
let audioCtx = null;

/* ==========================================================================
   Severity → CSS class and display-text mapping
   ========================================================================== */
const SEVERITY_CLASS = {
  Low:    "severity-low",
  Medium: "severity-medium",
  High:   "severity-high",
  None:   "severity-none",
};

const SEVERITY_DISPLAY = {
  Low:    "Low",
  Medium: "Medium",
  High:   "HIGH",
  None:   "No Detection",
};

/* ==========================================================================
   Web Audio API beep — short 880 Hz tone, ~200 ms
   Does not reference any external audio file.
   Wrapped in try/catch so a blocked AudioContext never crashes polling.
   ========================================================================== */
function playAlertBeep() {
  try {
    if (!audioCtx) {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    }
    // Resume in case the browser suspended the context (autoplay policy).
    audioCtx.resume().then(() => {
      const oscillator = audioCtx.createOscillator();
      const gainNode   = audioCtx.createGain();

      oscillator.connect(gainNode);
      gainNode.connect(audioCtx.destination);

      oscillator.type      = "sine";
      oscillator.frequency.setValueAtTime(880, audioCtx.currentTime);

      // Quick fade-out to avoid a click artefact at the end.
      gainNode.gain.setValueAtTime(0.5, audioCtx.currentTime);
      gainNode.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.2);

      oscillator.start(audioCtx.currentTime);
      oscillator.stop(audioCtx.currentTime + 0.2);
    });
  } catch (err) {
    // Audio blocked by browser policy or not supported — log and continue.
    console.warn("[dashboard] playAlertBeep failed:", err);
  }
}

/* ==========================================================================
   Update the severity badge
   ========================================================================== */
function updateSeverityBadge(severity) {
  const cssClass   = SEVERITY_CLASS[severity]   || "severity-none";
  const displayTxt = SEVERITY_DISPLAY[severity] || severity;
  severityBadge.className  = cssClass;   // replaces ALL classes in one assignment
  severityBadge.textContent = displayTxt;
}

/* ==========================================================================
   Update the alert banner (shown only while severity === "High")
   Beep fires ONLY on the transition INTO "High".
   ========================================================================== */
function updateAlertBanner(currentSeverity) {
  const isHigh = currentSeverity === "High";

  if (isHigh) {
    // Show the banner whenever we are in High state.
    alertBanner.classList.remove("hidden");

    // Beep only once — on the transition from non-High to High.
    if (prevSeverity !== "High") {
      playAlertBeep();
    }
  } else {
    // Hide the banner the moment severity drops out of High.
    alertBanner.classList.add("hidden");
  }
}

/* ==========================================================================
   Prepend a new entry to the event list.
   Cap the list at 20 visible entries (remove oldest / bottom entry).
   ========================================================================== */
function addEventEntry(data) {
  // Format the ISO timestamp to a compact local time for the log column.
  let timeDisplay;
  try {
    const d = new Date(data.timestamp);
    timeDisplay = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch (_) {
    timeDisplay = data.timestamp;
  }

  const severityClass = SEVERITY_CLASS[data.severity] || "severity-none";
  const confidencePct = (parseFloat(data.confidence) * 100).toFixed(1);

  const li = document.createElement("li");
  li.className = `event-entry ${severityClass}`;
  li.innerHTML =
    `<span class="event-time">${timeDisplay}</span>` +
    `<span class="event-class">${data.class}</span>` +
    `<span class="event-sev">${data.severity}</span>` +
    `<span class="event-confidence">${confidencePct}%</span>`;

  // Prepend so newest is always at the top.
  eventList.insertBefore(li, eventList.firstChild);

  // Trim to 20 entries — remove from the bottom (oldest).
  while (eventList.children.length > 20) {
    eventList.removeChild(eventList.lastChild);
  }
}

/* ==========================================================================
   Core poll handler — called with parsed JSON on each successful fetch.
   ========================================================================== */
function handleDetection(data) {
  const currentSeverity = data.severity || "None";
  const detClass        = data.class    || "none";
  const timestamp       = data.timestamp;

  // --- Always update: severity badge and alert banner (live state) ----------
  updateSeverityBadge(currentSeverity);
  updateAlertBanner(currentSeverity);

  // --- Pipeline mode indicator (Phase 12: show fallback warning when mock) --
  if (pipelineStatus) {
    if (data.pipeline_mode === "mock") {
      pipelineStatus.classList.remove("hidden");
    } else {
      pipelineStatus.classList.add("hidden");
    }
  }


  // --- De-duplicated update: counter + event log ----------------------------
  // Only increment and log when:
  //   1. The detection is a real event (class !== "none")
  //   2. The timestamp is different from the last one we already processed.
  //      (During the 4-s mock window, the same timestamp repeats — skip those.)
  const isNewEvent = (detClass !== "none") && (timestamp !== lastTimestamp);

  if (isNewEvent) {
    lastTimestamp = timestamp;
    sessionCount += 1;
    detectionCounter.textContent = sessionCount;
    addEventEntry(data);
  }

  // --- Advance state for next poll ------------------------------------------
  prevSeverity = currentSeverity;
}

/* ==========================================================================
   Polling loop — setInterval at 1 000 ms
   ========================================================================== */
function startPolling() {
  setInterval(async () => {
    try {
      const response = await fetch("/latest_detection");
      if (!response.ok) {
        // HTTP error (4xx/5xx) — log and skip this cycle silently.
        console.warn(`[dashboard] /latest_detection returned HTTP ${response.status}`);
        return;
      }
      const data = await response.json();
      handleDetection(data);
    } catch (err) {
      // Network error (server down, timeout, etc.) — log, do not crash the loop.
      console.warn("[dashboard] Fetch error on /latest_detection:", err);
    }
  }, 1000);
}

/* ==========================================================================
   Entry point
   ========================================================================== */
document.addEventListener("DOMContentLoaded", () => {
  startPolling();
  initVideoSwitcher();
});

/* ==========================================================================
   Video-source switcher
   Sends POST /switch_video and forces the MJPEG <img> to reload its src so
   the browser drops the old stream and reconnects to the new one immediately.
   ========================================================================== */
function initVideoSwitcher() {
  const buttons       = document.querySelectorAll(".switcher-btn");
  const statusEl      = document.getElementById("switcher-status");
  const videoFeedImg  = document.getElementById("video-feed");

  if (!buttons.length || !videoFeedImg) return;

  buttons.forEach((btn) => {
    btn.addEventListener("click", async () => {
      const video = btn.dataset.video;
      if (!video) return;

      // Disable all buttons while the request is in flight.
      buttons.forEach((b) => (b.disabled = true));
      statusEl.textContent = "Switching…";
      statusEl.className   = "switcher-status";

      try {
        const res = await fetch("/switch_video", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({ video }),
        });

        const data = await res.json();

        if (res.ok && data.status === "ok") {
          // Update active button state.
          buttons.forEach((b) => {
            b.classList.toggle("active", b === btn);
            b.setAttribute("aria-pressed", b === btn ? "true" : "false");
          });

          // Force the MJPEG img to reconnect: append a cache-buster so the
          // browser treats it as a new request and drops the previous stream.
          const base = videoFeedImg.src.split("?")[0];
          videoFeedImg.src = `${base}?t=${Date.now()}`;

          statusEl.textContent = `✓ Now showing: ${btn.textContent.trim()}`;
          statusEl.className   = "switcher-status ok";
        } else {
          statusEl.textContent = `✗ ${data.message || "Switch failed"}`;
          statusEl.className   = "switcher-status error";
        }
      } catch (err) {
        console.warn("[switcher] fetch error:", err);
        statusEl.textContent = "✗ Network error — could not switch source";
        statusEl.className   = "switcher-status error";
      } finally {
        // Re-enable all buttons.
        buttons.forEach((b) => (b.disabled = false));
        // Clear the status message after 3 s.
        setTimeout(() => {
          statusEl.textContent = "";
          statusEl.className   = "switcher-status";
        }, 3000);
      }
    });
  });
}
