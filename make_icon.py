"""Genera icon.ico (cuadrado redondeado con degradé cian-violeta y un rayo)."""
from PIL import Image, ImageDraw

S = 512
grad = Image.new("RGB", (S, S))
px = grad.load()
c1, c2 = (34, 211, 238), (139, 92, 246)
for y in range(S):
    for x in range(S):
        t = (x + y) / (2 * S)
        px[x, y] = tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))

mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle((0, 0, S - 1, S - 1), radius=110, fill=255)
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
img.paste(grad, (0, 0), mask)

bolt = [(300, 60), (130, 290), (240, 290), (205, 452), (385, 210), (270, 210)]
ImageDraw.Draw(img).polygon(bolt, fill=(255, 255, 255, 255))
img.save("icon.ico", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
img.resize((256, 256)).save("icon_preview.png")
print("icon.ico listo")
