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
});
