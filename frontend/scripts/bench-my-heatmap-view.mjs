#!/usr/bin/env node
/**
 * Bytes a /map view actually pulls from a per-user "Ma heatmap" PMTiles
 * (docs/ma-heatmap-fichier-prive.md §8), using the SAME pmtiles client the
 * browser runs: header + directories + the tiles covering the viewport.
 *
 *   node scripts/bench-my-heatmap-view.mjs <file.pmtiles> [lon lat] [width height]
 *
 * Defaults: centre = Montpellier (where backend/scripts/bench_my_heatmap.py
 * draws its synthetic rides), viewport 1440×900 CSS px, MapLibre 512 px vector
 * tiles. Reports, per zoom, the number of Range requests, bytes, and the
 * decoded tile sizes.
 */
import { open } from 'node:fs/promises';
import { PMTiles } from 'pmtiles';

const [file, lonArg, latArg, wArg, hArg] = process.argv.slice(2);
if (!file) {
  console.error('usage: bench-my-heatmap-view.mjs <file.pmtiles> [lon lat] [width height]');
  process.exit(2);
}
const lon = Number(lonArg ?? 3.8767);
const lat = Number(latArg ?? 43.6108);
const W = Number(wArg ?? 1440);
const H = Number(hArg ?? 900);

class CountingFileSource {
  constructor(fh) { this.fh = fh; this.requests = 0; this.bytes = 0; }
  getKey() { return file; }
  async getBytes(offset, length) {
    const buf = Buffer.alloc(length);
    const { bytesRead } = await this.fh.read(buf, 0, length, offset);
    this.requests += 1;
    this.bytes += bytesRead;
    return { data: buf.buffer.slice(buf.byteOffset, buf.byteOffset + bytesRead) };
  }
}

function tileXY(lonDeg, latDeg, z) {
  const n = 2 ** z;
  const x = ((lonDeg + 180) / 360) * n;
  const r = (latDeg * Math.PI) / 180;
  const y = ((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * n;
  return [x, y];
}

const fh = await open(file, 'r');
for (const z of [8, 10, 12, 13, 14]) {
  // Fresh client per zoom = a cold page load at that zoom.
  const src = new CountingFileSource(fh);
  const p = new PMTiles(src);
  await p.getHeader();
  const [cx, cy] = tileXY(lon, lat, z);
  const halfW = W / 2 / 512;
  const halfH = H / 2 / 512;
  let tiles = 0;
  let tileBytes = 0;
  const n = 2 ** z;
  for (let x = Math.floor(cx - halfW); x <= Math.floor(cx + halfW); x += 1) {
    for (let y = Math.floor(cy - halfH); y <= Math.floor(cy + halfH); y += 1) {
      if (x < 0 || y < 0 || x >= n || y >= n) continue;
      const t = await p.getZxy(z, x, y);
      if (t) { tiles += 1; tileBytes += t.data.byteLength; }
    }
  }
  console.log(JSON.stringify({
    zoom: z, tiles, requests: src.requests,
    transferred_kb: Math.round(src.bytes / 1024),
    decoded_tiles_kb: Math.round(tileBytes / 1024),
  }));
}
await fh.close();
