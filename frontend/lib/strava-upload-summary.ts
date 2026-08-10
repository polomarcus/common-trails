/**
 * Pure formatter for the manual GPX/FIT/ZIP upload result summary.
 *
 * Extracted from app/strava/page.tsx so the "X importée(s) · Y ignorée(s) · Z
 * erreur(s)" string assembly is unit-testable without a browser. Manual uploads
 * ALWAYS feed the community heatmap (the compliant, ODbL, consented path), so a
 * successful import appends the reassuring `strava.heatNote` tail.
 */
export interface UploadCounts {
  imported: number;
  skipped: number;
  failed: number;
}

type Translate = (key: string, vars?: Record<string, string | number>) => string;

export function buildUploadSummary(c: UploadCounts, t: Translate): string {
  const parts: string[] = [];
  if (c.imported > 0) {
    parts.push(t('strava.resultImported', { count: c.imported, s: c.imported > 1 ? 's' : '' }));
  }
  if (c.skipped > 0) {
    parts.push(t('strava.resultSkipped', { count: c.skipped, s: c.skipped > 1 ? 's' : '' }));
  }
  if (c.failed > 0) {
    parts.push(t('strava.resultFailed', { count: c.failed, s: c.failed > 1 ? 's' : '' }));
  }
  const heatNote = c.imported > 0 ? t('strava.heatNote') : '';
  return parts.join(' · ') + heatNote;
}
