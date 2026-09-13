from ultralytics import YOLO

model = YOLO("model_weights/fire_yolo.pt")
results = model(["test_images/fire.jpg", "test_images/smoke.jpg", "test_images/none.jpg"])
for r in results:
    print(r.boxes)