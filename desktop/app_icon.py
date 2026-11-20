import os
from pathlib import Path
from PIL import Image, ImageDraw

def create_default_icon(size: int = 128) -> Image.Image:
    """Генерирует стильную фирменную иконку агента (синий градиент + молния ⚡)."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Круглый фон с глубоким синим индиго
    padding = size // 16
    draw.ellipse(
        [padding, padding, size - padding, size - padding],
        fill=(26, 35, 126, 255),  # Deep Indigo
        outline=(66, 165, 245, 255),  # Electric Blue
        width=max(2, size // 32)
    )

    # Молния (Lightning bolt) по центру золотисто-белого цвета
    cx = size // 2
    cy = size // 2
    scale = size / 64.0

    points = [
        (cx + int(2 * scale), cy - int(20 * scale)),
        (cx - int(14 * scale), cy + int(2 * scale)),
        (cx - int(2 * scale), cy + int(2 * scale)),
        (cx - int(6 * scale), cy + int(20 * scale)),
        (cx + int(14 * scale), cy - int(2 * scale)),
        (cx + int(2 * scale), cy - int(2 * scale)),
    ]
    draw.polygon(points, fill=(255, 214, 0, 255))  # Amber / Gold

    return img

def ensure_icon_file(filepath: Path = None) -> Path:
    if filepath is None:
        filepath = Path(__file__).parent / "assets" / "icon.png"
    filepath.parent.mkdir(parents=True, exist_ok=True)
    if not filepath.exists():
        icon = create_default_icon(256)
        icon.save(filepath, format="PNG")
    return filepath

if __name__ == "__main__":
    p = ensure_icon_file()
    print(f"Icon generated at: {p}")
