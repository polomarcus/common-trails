import { describe, expect, it } from 'vitest';
import fs from 'fs';
import path from 'path';

/**
 * Non-régression (2026-11-11) : `useHeatmapControl` enregistrait encore
 * `/tile-cache-sw.js`, supprimé de `public/` depuis longtemps → un 404 et une
 * erreur console sur chaque page avec la carte. Tout script référencé par
 * `serviceWorker.register(...)` ou `new Worker(...)` doit exister dans `public/`.
 */
const ROOT = path.resolve(__dirname, '..', '..');
const DIRS = ['app', 'components', 'hooks', 'lib'];
const REF = /(?:serviceWorker\s*\.\s*register|new\s+Worker)\(\s*[`'"]([^`'"]+)[`'"]/g;

function sources(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) return e.name === '__tests__' || e.name === 'node_modules' ? [] : sources(p);
    return /\.(ts|tsx)$/.test(e.name) ? [p] : [];
  });
}

describe('scripts publics référencés par le code', () => {
  it('chaque service worker / worker enregistré existe dans public/', () => {
    const missing: string[] = [];
    for (const file of DIRS.flatMap((d) => sources(path.join(ROOT, d)))) {
      for (const m of fs.readFileSync(file, 'utf8').matchAll(REF)) {
        // `${basePath}/x.js` → `/x.js`
        const rel = m[1].replace(/^\$\{[^}]+\}/, '').split('?')[0];
        if (!rel.startsWith('/')) continue;
        if (!fs.existsSync(path.join(ROOT, 'public', rel))) {
          missing.push(`${path.relative(ROOT, file)} → ${rel}`);
        }
      }
    }
    expect(missing).toEqual([]);
  });
});
