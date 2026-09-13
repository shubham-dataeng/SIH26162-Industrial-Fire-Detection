from huggingface_hub import hf_hub_download
import shutil

ckpt = hf_hub_download(repo_id="rabahdev/fire-smoke-yolov8n", filename="best.pt")
shutil.copy(ckpt, "model_weights/fire_yolo.pt")
print("Saved to model_weights/fire_yolo.pt")