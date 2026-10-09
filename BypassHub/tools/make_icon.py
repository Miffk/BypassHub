"""Генерация assets/icon.ico: каждый размер рисуется отдельно.

Мелкие размеры (16–32 px) упрощены и сделаны крупнее: Windows показывает их
в панели задач и в трее, а уменьшение большой картинки даёт «мыльный» значок.
Запуск: python tools/make_icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

SS = 8  # суперсэмплинг для гладких краёв
TOP, BOTTOM = (45, 132, 255), (20, 86, 210)
# 24/30/36 — значок панели задач при масштабе 100/125/150%, 16/20 — заголовок окна и трей
SIZES = [16, 20, 24, 30, 32, 36, 40, 48, 64, 96, 128, 256]


def render(size: int) -> Image.Image:
    n = size * SS
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    # фон: скруглённый квадрат во весь значок с вертикальным градиентом
    grad = Image.new("RGBA", (1, n))
    for y in range(n):
        t = y / (n - 1)
        grad.putpixel((0, y), tuple(int(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3)) + (255,))
    grad = grad.resize((n, n))
    mask = Image.new("L", (n, n), 0)
    margin = 0 if size <= 36 else n * 0.03
    ImageDraw.Draw(mask).rounded_rectangle([margin, margin, n - 1 - margin, n - 1 - margin],
                                           radius=n * 0.22, fill=255)
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)

    def P(x, y):  # координаты в долях значка
        return (x * n, y * n)

    small = size <= 36  # панель задач, трей, заголовок: тот же рисунок, но крупнее и проще
    if small:
        shield = [P(.5, .10), P(.85, .22), P(.85, .50), P(.5, .92), P(.15, .50), P(.15, .22)]
        k, cx, cy = 0.62, 0.5, 0.48
    else:
        shield = [P(.5, .15), P(.79, .25), P(.79, .5), P(.5, .86), P(.21, .5), P(.21, .25)]
        k, cx, cy = 0.52, 0.5, 0.47
    d.polygon(shield, fill=(255, 255, 255, 255))
    plane_color, fold = (37, 120, 245, 255), (20, 80, 200, 255)
    # бумажный самолётик (координаты относительно центра)
    pts = [(-.40, .03), (.40, -.32), (.22, .40), (.04, .20), (-.07, .33), (-.08, .12)]
    tr = lambda x, y: P(cx + x * k, cy + y * k)  # noqa: E731
    d.polygon([tr(*p) for p in pts], fill=plane_color)
    if size > 20:  # на самых мелких размерах складка сливается — без неё чётче
        d.polygon([tr(-.08, .12), tr(.04, .20), tr(.40, -.32)], fill=fold)

    out = img.resize((size, size), Image.LANCZOS)
    if size <= 48:  # вернуть резкость краям после уменьшения
        rgb = out.convert("RGB").filter(ImageFilter.UnsharpMask(radius=0.6, percent=120, threshold=0))
        rgb.putalpha(out.getchannel("A"))
        out = rgb
    return out


def main() -> None:
    images = [render(s) for s in SIZES]
    target = Path(__file__).resolve().parent.parent / "assets" / "icon.ico"
    # кадры в классическом формате BMP — Windows читает их надёжнее, чем PNG внутри .ico
    images[-1].save(target, format="ICO", sizes=[(s, s) for s in SIZES], append_images=images[:-1],
                    bitmap_format="bmp")
    print("saved", target)


if __name__ == "__main__":
    main()
