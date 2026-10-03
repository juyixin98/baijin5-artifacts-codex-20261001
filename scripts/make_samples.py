"""Generate the sample PNGs in data/ from app.samples (deterministic)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from app.samples import SAMPLES

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    for name, builder in SAMPLES.items():
        image = builder()
        path = DATA_DIR / f"{name}.png"
        Image.fromarray((1 - image) * 255, mode="L").save(path)
        print(f"wrote {path} shape={image.shape} foreground={int(image.sum())}")


if __name__ == "__main__":
    main()
