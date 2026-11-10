"""MVT → PNG rasterisation for the shared "Ma heatmap" XYZ raster.

gpx.studio overlays, OsmAnd and Locus only take raster XYZ tiles, so
``/s/{token}/{z}/{x}/{y}.png`` renders, on demand, the (already
endpoint-masked) vector tile of the shared PMTiles: lines only, one cyan, on a
transparent background (docs/ma-heatmap-fichier-prive.md §10.3).

A minimal Mapbox Vector Tile decoder (protobuf by hand: layers → features →
command-encoded geometry) keeps this dependency-free; Pillow is already used
for the OG cards. Lines are drawn 2× supersampled then downscaled (cheap
anti-aliasing). Zooms above the archive's max zoom are rendered from the
max-zoom ancestor (the geometry is ~1 m precise, nothing is lost).
"""

from __future__ import annotations

import io

from PIL import Image, ImageDraw

TILE_PX = 256
_SS = 2  # supersampling factor
# MY_TRACES_COLOR in frontend/lib/constants.ts ("Ma heatmap" cyan).
LINE_RGB = (6, 182, 212)
LINE_ALPHA = 225


def _varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7


def _fields(buf: bytes):
    """Yield (field_number, wire_type, value) — value is int or bytes."""
    pos, n = 0, len(buf)
    while pos < n:
        key, pos = _varint(buf, pos)
        field, wt = key >> 3, key & 7
        if wt == 0:
            v, pos = _varint(buf, pos)
            yield field, wt, v
        elif wt == 2:
            ln, pos = _varint(buf, pos)
            yield field, wt, buf[pos:pos + ln]
            pos += ln
        elif wt == 1:
            yield field, wt, buf[pos:pos + 8]
            pos += 8
        elif wt == 5:
            yield field, wt, buf[pos:pos + 4]
            pos += 4
        else:
            raise ValueError(f"unsupported wire type {wt}")


def _packed(buf: bytes) -> list[int]:
    out, pos = [], 0
    while pos < len(buf):
        v, pos = _varint(buf, pos)
        out.append(v)
    return out


def _zigzag(n: int) -> int:
    return (n >> 1) ^ -(n & 1)


def _decode_geometry(cmds: list[int]) -> list[list[tuple[int, int]]]:
    lines: list[list[tuple[int, int]]] = []
    cur: list[tuple[int, int]] = []
    x = y = 0
    i = 0
    while i < len(cmds):
        cmd, count = cmds[i] & 7, cmds[i] >> 3
        i += 1
        if cmd == 7:  # ClosePath
            if cur:
                cur.append(cur[0])
            continue
        for _ in range(count):
            if i + 1 >= len(cmds):
                break  # truncated geometry: keep what was decoded
            x += _zigzag(cmds[i])
            y += _zigzag(cmds[i + 1])
            i += 2
            if cmd == 1:  # MoveTo
                if len(cur) >= 2:
                    lines.append(cur)
                cur = [(x, y)]
            else:  # LineTo
                cur.append((x, y))
    if len(cur) >= 2:
        lines.append(cur)
    return lines


def decode_lines(tile: bytes) -> list[tuple[int, list[list[tuple[int, int]]]]]:
    """All line/polygon geometries of every layer → [(extent, lines)]."""
    out = []
    for field, wt, layer in _fields(tile):
        if field != 3 or wt != 2:
            continue
        extent = 4096
        geoms = []
        for lf, lwt, val in _fields(layer):
            if lf == 5 and lwt == 0:
                extent = val
            elif lf == 2 and lwt == 2:
                gtype, cmds = 0, b""
                for ff, fwt, fv in _fields(val):
                    if ff == 3 and fwt == 0:
                        gtype = fv
                    elif ff == 4 and fwt == 2:
                        cmds = fv
                if gtype in (2, 3) and cmds:
                    geoms.extend(_decode_geometry(_packed(cmds)))
        out.append((extent, geoms))
    return out


def _line_width(z: int) -> float:
    if z <= 9:
        return 1.0
    if z <= 12:
        return 1.5
    if z <= 14:
        return 2.5
    return 3.5


def render_png(tile: bytes | None, z: int, dz: int = 0, sub_x: int = 0, sub_y: int = 0) -> bytes:
    """PNG of ``tile`` (decompressed MVT, or None → transparent).

    ``dz``/``sub_x``/``sub_y``: render the sub-square (sub_x, sub_y) of a
    2^dz × 2^dz split of the tile (over-zoom past the archive's max zoom).
    """
    size = TILE_PX * _SS
    mask = Image.new("L", (size, size), 0)
    if tile:
        draw = ImageDraw.Draw(mask)
        w = max(1, round(_line_width(z) * _SS))
        k = 1 << dz
        for extent, lines in decode_lines(tile):
            scale = size * k / extent
            ox, oy = sub_x * size, sub_y * size
            for line in lines:
                pts = [(px * scale - ox, py * scale - oy) for px, py in line]
                draw.line(pts, fill=255, width=w, joint="curve")
    mask = mask.resize((TILE_PX, TILE_PX), Image.Resampling.LANCZOS)
    alpha = mask.point(lambda v: v * LINE_ALPHA // 255)
    img = Image.new("RGBA", (TILE_PX, TILE_PX), (*LINE_RGB, 0))
    img.putalpha(alpha)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False, compress_level=6)
    return buf.getvalue()
