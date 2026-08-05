"""Generate Foolad Tabar favicon PNG/ICO assets from the brand mark."""
from pathlib import Path
from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent
OUT.mkdir(parents=True, exist_ok=True)


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def steel_color(y, y0, y1):
    stops = [
        (0.00, (255, 255, 255)),
        (0.32, (200, 209, 220)),
        (0.62, (228, 233, 240)),
        (1.00, (138, 155, 176)),
    ]
    t = 0 if y1 <= y0 else max(0.0, min(1.0, (y - y0) / (y1 - y0)))
    for i in range(len(stops) - 1):
        t0, c0 = stops[i]
        t1, c1 = stops[i + 1]
        if t0 <= t <= t1:
            u = 0 if t1 == t0 else (t - t0) / (t1 - t0)
            return lerp(c0, c1, u)
    return stops[-1][1]


def draw_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    for y in range(size):
        for x in range(size):
            u = (x + y) / (2 * (size - 1)) if size > 1 else 0
            c = lerp((26, 35, 112), (10, 22, 41), u)
            img.putpixel((x, y), c + (255,))

    radius = max(2, int(round(size * 14 / 64)))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)

    def s(v):
        return v * size / 64.0

    # Top bar
    x0, y0, x1, y1 = s(11), s(13), s(11 + 42), s(13 + 7.5)
    rr = max(1, int(round(s(1.6))))
    bar = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bar_mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(bar_mask).rounded_rectangle([x0, y0, x1, y1], radius=rr, fill=255)
    for y in range(size):
        if y < y0 or y > y1:
            continue
        col = steel_color(y, y0, y1)
        for x in range(size):
            if bar_mask.getpixel((x, y)):
                bar.putpixel((x, y), col + (255,))
    out.alpha_composite(bar)

    # Angular stem (brand F-mark)
    pts = [
        (s(26), s(27.5)),
        (s(50), s(27.5)),
        (s(50), s(36.5)),
        (s(35.5), s(36.5)),
        (s(35.5), s(51)),
        (s(26), s(51)),
    ]
    stem_mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(stem_mask).polygon(pts, fill=255)
    stem = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    y_min, y_max = s(27.5), s(51)
    for y in range(size):
        for x in range(size):
            if stem_mask.getpixel((x, y)):
                stem.putpixel((x, y), steel_color(y, y_min, y_max) + (255,))
    out.alpha_composite(stem)
    return out


def main():
    for size, name in [
        (16, "favicon-16.png"),
        (32, "favicon-32.png"),
        (48, "favicon-48.png"),
        (180, "apple-touch-icon.png"),
    ]:
        draw_icon(size).save(OUT / name, "PNG")
        print("wrote", name)

    sizes = [16, 32, 48]
    imgs = [draw_icon(s) for s in sizes]
    imgs[0].save(
        OUT / "favicon.ico",
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=imgs[1:],
    )
    print("wrote favicon.ico")


if __name__ == "__main__":
    main()
