r"""Cut the logo out of its white background: docs/logo/paper-plane-logo.webp (the owner's
picture, white background) -> docs/logo/paper-plane-logo.png (see-through background).

    cd C:\Users\USER\Documents\Workspace\chat_app
    .venv\Scripts\python docs\cut_out_logo.py

How (Pillow only, no numpy):
1. The background is the near-white (every channel >= 225) that touches the picture's
   edge, found with a flood fill, so white INSIDE the plane (its light stripe) stays.
   Specks of compression noise left in it (smaller than SPECK pixels) join it too.
2. The few pixels on the plane's edge (RING px) are a mix of the plane's colour and white.
   Each one takes the colour of the nearest solid pixel of the plane, and an opacity that
   says how much of that colour it holds, so the edge stays smooth with no white fringe
   on a dark or coloured background.
Then run docs/make_icons.py (it uses the PNG).
"""
from collections import deque
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).parent.parent
SOURCE = ROOT / "docs" / "logo" / "paper-plane-logo.webp"
OUT = ROOT / "docs" / "logo" / "paper-plane-logo.png"
WHITE_FROM = 225   # the plane's palest part (the trail's last dot) is far below this
RING = 3
SPECK = 60         # smaller bits left in the background are compression noise


def main():
    picture = Image.open(SOURCE).convert("RGB")
    width, height = picture.size
    pixels = picture.load()

    def near_white(x, y):
        return min(pixels[x, y]) >= WHITE_FROM

    # 1. The background: near-white pixels joined to the edge.
    background = bytearray(width * height)
    queue = deque()
    for x in range(width):
        for y in (0, height - 1):
            queue.append((x, y))
    for y in range(height):
        for x in (0, width - 1):
            queue.append((x, y))
    while queue:
        x, y = queue.popleft()
        index = y * width + x
        if background[index] or not near_white(x, y):
            continue
        background[index] = 1
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < width and 0 <= ny < height and not background[ny * width + nx]:
                queue.append((nx, ny))

    # Specks: small islands that aren't background (compression noise around the edges).
    seen = bytearray(width * height)
    for start in range(width * height):
        if background[start] or seen[start]:
            continue
        part, queue = [], deque([start])
        seen[start] = 1
        while queue:
            index = queue.popleft()
            part.append(index)
            x, y = index % width, index // width
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if 0 <= nx < width and 0 <= ny < height:
                    other = ny * width + nx
                    if not background[other] and not seen[other]:
                        seen[other] = 1
                        queue.append(other)
        if len(part) < SPECK:
            for index in part:
                background[index] = 1

    # 2. The edge ring: plane pixels within RING steps of the background.
    distance = [-1] * (width * height)
    queue = deque()
    for index, is_background in enumerate(background):
        if is_background:
            distance[index] = 0
            queue.append(index)
    while queue:
        index = queue.popleft()
        if distance[index] >= RING:
            continue
        x, y = index % width, index // width
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < width and 0 <= ny < height:
                other = ny * width + nx
                if distance[other] == -1:
                    distance[other] = distance[index] + 1
                    queue.append(other)

    # Each ring pixel's nearest solid plane colour (spread inwards-out from the solid part).
    solid = {}
    queue = deque()
    for index in range(width * height):
        if not background[index] and distance[index] == -1:
            x, y = index % width, index // width
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if 0 <= nx < width and 0 <= ny < height and distance[ny * width + nx] > 0:
                    solid[index] = pixels[x, y]
                    queue.append(index)
                    break
    while queue:
        index = queue.popleft()
        x, y = index % width, index // width
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < width and 0 <= ny < height:
                other = ny * width + nx
                if distance[other] > 0 and other not in solid:
                    solid[other] = solid[index]
                    queue.append(other)

    out = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    out_pixels = out.load()
    for index in range(width * height):
        if background[index]:
            continue
        x, y = index % width, index // width
        colour = pixels[x, y]
        if distance[index] > 0 and index in solid:
            # How much of the plane's colour this edge pixel holds (0 = white, 1 = all colour).
            fg = solid[index]
            span = sum((255 - c) ** 2 for c in fg)
            share = sum((255 - p) * (255 - c) for p, c in zip(colour, fg)) / span if span else 1
            share = max(0.0, min(1.0, share))
            out_pixels[x, y] = (*fg, round(share * 255))
        else:
            out_pixels[x, y] = (*colour, 255)
    out = out.crop(out.getbbox())
    out.save(OUT, optimize=True)
    print("saved", OUT.name, out.size)


if __name__ == "__main__":
    main()
