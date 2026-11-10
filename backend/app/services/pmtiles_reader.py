"""Minimal PMTiles v3 reader over a byte-range source (no dependency).

Used by the shared "Ma heatmap" link (``app.api.shared_heatmap``) to serve
``/s/{token}/{z}/{x}/{y}.pbf`` (and the on-demand PNG) out of the masked
PMTiles stored in the private bucket: one cached directory walk per version,
then one Range read per tile. Spec: https://github.com/protomaps/PMTiles
(v3 header = 127 bytes; directories = varint columns, optionally gzip'd;
tile ids along a Hilbert curve, z by z).

Directories are cached per object key (objects are immutable per version).
The cache never decides WHO may read: the token → user → key resolution is a
DB lookup on every request, before this module is reached.
"""

from __future__ import annotations

import gzip
import struct
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

HEADER_LEN = 127
# Root directory + header are guaranteed to fit in the first 16 KiB.
_FIRST_READ = 16384
_COMPRESSION_NONE = 1
_COMPRESSION_GZIP = 2

ReadFn = Callable[[int, int], bytes | None]  # (start, end inclusive) -> bytes


@dataclass(frozen=True)
class Header:
    root_offset: int
    root_length: int
    leaf_offset: int
    tile_data_offset: int
    internal_compression: int
    tile_compression: int
    tile_type: int
    min_zoom: int
    max_zoom: int


@dataclass(frozen=True)
class Entry:
    tile_id: int
    offset: int
    length: int
    run_length: int


class PMTilesError(ValueError):
    pass


def parse_header(buf: bytes) -> Header:
    if len(buf) < HEADER_LEN or buf[:7] != b"PMTiles" or buf[7] != 3:
        raise PMTilesError("not a PMTiles v3 archive")
    q = struct.unpack_from("<QQQQQQQQ", buf, 8)
    return Header(
        root_offset=q[0],
        root_length=q[1],
        leaf_offset=q[4],
        tile_data_offset=q[6],
        internal_compression=buf[97],
        tile_compression=buf[98],
        tile_type=buf[99],
        min_zoom=buf[100],
        max_zoom=buf[101],
    )


def _varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if pos >= len(buf):
            raise PMTilesError("truncated varint")
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7
        if shift > 63:
            raise PMTilesError("varint too long")


def _decompress(data: bytes, compression: int) -> bytes:
    if compression == _COMPRESSION_GZIP:
        return gzip.decompress(data)
    if compression in (_COMPRESSION_NONE, 0):
        return data
    raise PMTilesError(f"unsupported internal compression {compression}")


def parse_directory(data: bytes) -> list[Entry]:
    n, pos = _varint(data, 0)
    ids, runs, lens, offs = [], [], [], []
    last = 0
    for _ in range(n):
        v, pos = _varint(data, pos)
        last += v
        ids.append(last)
    for _ in range(n):
        v, pos = _varint(data, pos)
        runs.append(v)
    for _ in range(n):
        v, pos = _varint(data, pos)
        lens.append(v)
    for i in range(n):
        v, pos = _varint(data, pos)
        offs.append(offs[i - 1] + lens[i - 1] if v == 0 and i > 0 else v - 1)
    return [Entry(ids[i], offs[i], lens[i], runs[i]) for i in range(n)]


def zxy_to_tile_id(z: int, x: int, y: int) -> int:
    """PMTiles tile id: tiles of all lower zooms, then the Hilbert index."""
    if z < 0 or z > 26 or not (0 <= x < (1 << z) and 0 <= y < (1 << z)):
        raise PMTilesError("tile out of range")
    acc = ((1 << (2 * z)) - 1) // 3  # sum of 4^i for i < z
    n = 1 << z
    d = 0
    s = n >> 1
    while s > 0:
        rx = 1 if x & s else 0
        ry = 1 if y & s else 0
        d += s * s * ((3 * rx) ^ ry)
        if ry == 0:
            if rx == 1:
                x = n - 1 - x
                y = n - 1 - y
            x, y = y, x
        s >>= 1
    return acc + d


def find_entry(entries: list[Entry], tile_id: int) -> Entry | None:
    lo, hi = 0, len(entries) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        c = entries[mid].tile_id
        if c < tile_id:
            lo = mid + 1
        elif c > tile_id:
            hi = mid - 1
        else:
            return entries[mid]
    # hi = last entry with tile_id < target: a run or a leaf directory.
    if hi >= 0:
        e = entries[hi]
        if e.run_length == 0 or tile_id - e.tile_id < e.run_length:
            return e
    return None


class _LRU:
    def __init__(self, size: int) -> None:
        self.size = size
        self._d: OrderedDict = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            if key in self._d:
                self._d.move_to_end(key)
                return self._d[key]
        return None

    def put(self, key, value) -> None:
        with self._lock:
            self._d[key] = value
            self._d.move_to_end(key)
            while len(self._d) > self.size:
                self._d.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._d.clear()


_headers = _LRU(64)  # key -> (Header, root entries)
_leaves = _LRU(256)  # (key, offset, length) -> entries


def _read(read: ReadFn, start: int, length: int) -> bytes:
    data = read(start, start + length - 1)
    if data is None:
        raise FileNotFoundError("pmtiles object missing")
    return data


def header_and_root(cache_key: str, read: ReadFn) -> tuple[Header, list[Entry]]:
    hit = _headers.get(cache_key)
    if hit is not None:
        return hit
    first = _read(read, 0, _FIRST_READ)
    h = parse_header(first)
    end = h.root_offset + h.root_length
    raw = first[h.root_offset:end] if end <= len(first) else _read(read, h.root_offset, h.root_length)
    root = parse_directory(_decompress(raw, h.internal_compression))
    _headers.put(cache_key, (h, root))
    return h, root


def get_tile(cache_key: str, read: ReadFn, z: int, x: int, y: int) -> tuple[Header, bytes | None]:
    """Return (header, stored tile bytes or None). The bytes are as stored —
    gzip'd when ``header.tile_compression == 2``."""
    h, entries = header_and_root(cache_key, read)
    if z < h.min_zoom or z > h.max_zoom:
        return h, None
    tid = zxy_to_tile_id(z, x, y)
    for _depth in range(4):
        e = find_entry(entries, tid)
        if e is None:
            return h, None
        if e.run_length > 0:
            return h, _read(read, h.tile_data_offset + e.offset, e.length)
        lkey = (cache_key, e.offset, e.length)
        leaf = _leaves.get(lkey)
        if leaf is None:
            raw = _read(read, h.leaf_offset + e.offset, e.length)
            leaf = parse_directory(_decompress(raw, h.internal_compression))
            _leaves.put(lkey, leaf)
        entries = leaf
    return h, None


def clear_cache() -> None:
    _headers.clear()
    _leaves.clear()
