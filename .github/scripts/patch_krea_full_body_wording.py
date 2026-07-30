from pathlib import Path

path = Path("app/services/generated_image_qa_service.py")
text = path.read_text(encoding="utf-8")
old = "Correct the framing exactly: portrait 4:5 full-body mirror composition; entire head-to-feet figure inside the frame; visible headroom above the hair and visible floor below both feet; both feet fully visible; subject no more than about 70 percent of frame height; camera farther away; no close-up and no crop."
new = "Correct the framing exactly: full body visible in a portrait 4:5 mirror composition; entire head-to-feet figure inside the frame; visible headroom above the hair and visible floor below both feet; both feet fully visible; subject no more than about 70 percent of frame height; camera farther away; no close-up and no crop."
if old not in text:
    raise SystemExit("expected full-body correction text not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
