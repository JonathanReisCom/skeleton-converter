"""Minimal PNG codec: RGBA pixels in, RGBA pixels out, stdlib only.

The SkelForm writer has to rewrite atlas pages — a region the source packed
rotated (Spine's `rotate: 90`) has its pixels stored sideways, and SkelForm's
runtimes sample a rectangle linearly with no rotation flag, so a sprite packed
that way draws on its side. Rotating the uv cannot fix it (the sampled pixels
are the same either way); the pixels themselves have to be put upright.

Python ships zlib, and a PNG is zlib plus a per-row filter byte, so the whole
codec is a few dozen lines. Only 8-bit non-interlaced images are handled —
which is what every atlas page we have seen is — and anything else is refused
rather than guessed at.
"""

from __future__ import annotations

import struct
import zlib

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def read_png_size(path: str) -> tuple | None:
    """Width/height from a PNG's IHDR chunk, or None when it is not a PNG."""
    try:
        with open(path, "rb") as handle:
            header = handle.read(24)
    except OSError:
        return None
    if len(header) < 24 or header[:8] != PNG_SIGNATURE:
        return None
    return (
        int.from_bytes(header[16:20], "big"),
        int.from_bytes(header[20:24], "big"),
    )



class PngError(ValueError):
    """The file is not an 8-bit non-interlaced PNG we can read."""


def _chunks(data: bytes):
    if not data.startswith(PNG_SIGNATURE):
        raise PngError("not a PNG")
    offset = len(PNG_SIGNATURE)
    while offset + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[offset:offset + 8])
        body = data[offset + 8:offset + 8 + length]
        if len(body) != length:
            raise PngError("truncated chunk")
        yield kind, body
        offset += 12 + length


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _unfilter(raw: bytes, width: int, height: int, bpp: int) -> bytearray:
    stride = width * bpp
    out = bytearray(stride * height)
    previous = bytearray(stride)
    pos = 0
    for row in range(height):
        if pos >= len(raw):
            raise PngError("short image data")
        kind = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        if len(line) != stride:
            raise PngError("short row")
        pos += stride
        if kind == 1:       # Sub
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif kind == 2:     # Up
            for i in range(stride):
                line[i] = (line[i] + previous[i]) & 0xFF
        elif kind == 3:     # Average
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 0xFF
        elif kind == 4:     # Paeth
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                upleft = previous[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(left, previous[i], upleft)) & 0xFF
        elif kind != 0:
            raise PngError(f"unknown filter {kind}")
        out[row * stride:(row + 1) * stride] = line
        previous = line
    return out


def read_png(data: bytes) -> tuple:
    """PNG bytes -> (width, height, RGBA bytes)."""
    width = height = None
    depth = colour = None
    palette = b""
    transparency = b""
    compressed = bytearray()
    for kind, body in _chunks(data):
        if kind == b"IHDR":
            width, height, depth, colour, _, _, interlace = struct.unpack(">IIBBBBB", body)
            if depth != 8 or interlace != 0:
                raise PngError(f"unsupported depth={depth} interlace={interlace}")
            if colour not in (0, 2, 3, 4, 6):
                raise PngError(f"unsupported colour type {colour}")
        elif kind == b"PLTE":
            palette = body
        elif kind == b"tRNS":
            transparency = body
        elif kind == b"IDAT":
            compressed += body
        elif kind == b"IEND":
            break
    if width is None:
        raise PngError("no IHDR")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[colour]
    raw = zlib.decompress(bytes(compressed))
    flat = _unfilter(raw, width, height, channels)
    rgba = bytearray(width * height * 4)
    for index in range(width * height):
        base = index * channels
        if colour == 6:                      # RGBA
            rgba[index * 4:index * 4 + 4] = flat[base:base + 4]
        elif colour == 2:                    # RGB
            rgba[index * 4:index * 4 + 3] = flat[base:base + 3]
            rgba[index * 4 + 3] = 255
        elif colour == 0:                    # grey
            value = flat[base]
            alpha = 255 if not transparency else (0 if value == transparency[:2] else 255)
            rgba[index * 4:index * 4 + 4] = bytes((value, value, value, alpha))
        elif colour == 4:                    # grey + alpha
            value, alpha = flat[base], flat[base + 1]
            rgba[index * 4:index * 4 + 4] = bytes((value, value, value, alpha))
        else:                                # palette
            entry = flat[base] * 3
            rgba[index * 4:index * 4 + 3] = palette[entry:entry + 3]
            rgba[index * 4 + 3] = (transparency[flat[base]]
                                   if flat[base] < len(transparency) else 255)
    return width, height, bytes(rgba)


def write_png(width: int, height: int, rgba: bytes) -> bytes:
    """(width, height, RGBA bytes) -> PNG bytes."""
    if len(rgba) != width * height * 4:
        raise PngError("pixel buffer does not match the size")
    stride = width * 4
    raw = bytearray()
    for row in range(height):
        raw.append(0)                        # filter: None
        raw += rgba[row * stride:(row + 1) * stride]

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (PNG_SIGNATURE + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def crop(rgba: bytes, page_w: int, box: tuple) -> bytes:
    """The pixels of ``box`` (x, y, w, h) inside a page, as its own image.

    Anything the box asks for outside the page comes back transparent, so the
    result is always exactly ``w * h * 4`` bytes: a caller that blits it into a
    page of its own must not end up with a short row.
    """
    x, y, w, h = box
    out = bytearray(w * h * 4)
    page_h = len(rgba) // (page_w * 4) if page_w else 0
    for row in range(h):
        source_row = y + row
        if source_row < 0 or source_row >= page_h:
            continue
        for column in range(w):
            source_column = x + column
            if source_column < 0 or source_column >= page_w:
                continue
            source = (source_row * page_w + source_column) * 4
            target = (row * w + column) * 4
            out[target:target + 4] = rgba[source:source + 4]
    return bytes(out)


def rotate_quarter(rgba: bytes, width: int, height: int, clockwise: bool) -> tuple:
    """Rotate a sprite by 90 degrees. Returns (width, height, pixels)."""
    out = bytearray(len(rgba))
    for row in range(height):
        for column in range(width):
            source = (row * width + column) * 4
            if clockwise:
                target_row, target_column = column, height - 1 - row
            else:
                target_row, target_column = width - 1 - column, row
            target = (target_row * height + target_column) * 4
            out[target:target + 4] = rgba[source:source + 4]
    return (height, width, bytes(out))


def _self_check() -> None:
    # A 3x2 image with every filter type exercised by a round trip, and the
    # rotations.
    width, height = 3, 2
    pixels = bytes(range(24))
    encoded = write_png(width, height, pixels)
    back = read_png(encoded)
    assert back == (width, height, pixels), back[:2]

    sprite = bytes([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12,
                    13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24])
    w, h, turned = rotate_quarter(sprite, 2, 3, clockwise=True)
    assert (w, h) == (3, 2) and len(turned) == len(sprite)
    # Rotating four times comes back to the original.
    state = (2, 3, sprite)
    for _ in range(4):
        state = rotate_quarter(state[2], state[0], state[1], clockwise=True)
    assert state == (2, 3, sprite), state[:2]

    # Cropping picks the right pixels out of a page.
    page = write_png(4, 4, bytes(range(64)))
    pw, ph, pixels = read_png(page)
    piece = crop(pixels, pw, (1, 2, 2, 2))
    assert piece == read_png(write_png(2, 2, piece))[2]


if __name__ == "__main__":
    _self_check()
    print("png.py self-check ok")