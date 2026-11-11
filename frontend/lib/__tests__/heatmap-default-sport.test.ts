import { describe, expect, it } from 'vitest';
import fs from 'fs';
import path from 'path';
import { communityHeatSportFilter } from '../community-heatmap-layers';

/**
 * Non-régression (2026-11-11) : /map s'ouvrait filtrée sur « Off-road
 * (VTT+Gravel) » — un nouveau venu ne voyait ni la route ni la course à pied,
 * sans le savoir. Le filtre par défaut est « tous les sports ».
 */
describe('heatmap commune — sport par défaut', () => {
  it("useHeatmapControl démarre sur 'all', qui ne filtre rien", () => {
    const src = fs.readFileSync(path.resolve(__dirname, '../../hooks/useHeatmapControl.ts'), 'utf8');
    const m = src.match(/const \[heatmapSport, setHeatmapSport\] = useState<string>\('([a-z]+)'\)/);
    expect(m?.[1]).toBe('all');
    expect(communityHeatSportFilter('all')).toBeNull();
  });
});
