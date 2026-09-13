# A5 — Accuracy Pass Results

Tested `run_pipeline()` (backend/inference/detect.py) against 4 sample videos to check for false positives and false negatives before integration.

| Video | Scenario | Result | Notes |
|---|---|---|---|
| test.mp4 | Large raging fire | 99.3% High | Correctly escalates on sustained large fire |
| cooking_fire.mp4 | Small contained stove fire | 0% High (Low/Medium only) | Correctly avoids over-escalating a small localized fire |
| sunset_timelapse.mp4 | No fire, orange sky | 100% Low | No false positive from color alone |
| traffic_sunset.mp4 | No fire, headlights + sunset glare | 100% Low | No false positive from artificial lights/glare |

**Conclusion:** No false positives or false negatives observed across this test set. Confidence threshold (`CONF_FLOOR = 0.45`) and fire-persistence window (`FIRE_PERSIST_N = 5`) in `backend/severity/classifier.py` require no tuning at this stage.
