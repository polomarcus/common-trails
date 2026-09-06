/**
 * Translation dictionary parity + Strava-wizard key coverage.
 *
 * The mixed-language bug (French leaking into an otherwise-English UI, or a
 * key present in one dictionary but not the other) is caught here: t() falls
 * back to the French value — then the raw key — when a locale is missing a key
 * (see lib/i18n.tsx). Asserting fr/en have IDENTICAL key sets makes that class
 * of bug a red test instead of a production surprise.
 *
 * We drive the REAL dictionaries (no inline mirror), so any future key added to
 * one file but not the other fails immediately.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { describe, it, expect } from 'vitest';
import fr from '../translations/fr';
import en from '../translations/en';

describe('i18n dictionary parity', () => {
  it('fr and en expose an identical set of keys', () => {
    const frKeys = new Set(Object.keys(fr));
    const enKeys = new Set(Object.keys(en));
    const missingInEn = [...frKeys].filter((k) => !enKeys.has(k)).sort();
    const missingInFr = [...enKeys].filter((k) => !frKeys.has(k)).sort();
    expect(missingInEn, `keys present in fr but missing from en: ${missingInEn.join(', ')}`).toEqual([]);
    expect(missingInFr, `keys present in en but missing from fr: ${missingInFr.join(', ')}`).toEqual([]);
  });

  it('no dictionary value is left empty', () => {
    for (const [k, v] of Object.entries(fr)) expect(v, `fr[${k}] is empty`).not.toBe('');
    for (const [k, v] of Object.entries(en)) expect(v, `en[${k}] is empty`).not.toBe('');
  });
});

describe('Strava import wizard — every routed string exists in both locales', () => {
  // Keys that back the strings that used to be hardcoded in app/strava/page.tsx.
  // If a wizard string is un-routed again, add it here so the leak stays caught.
  const wizardKeys = [
    // Single-job contribution page (2026-07 compliance pivot — the personal
    // "connect Strava" flow was removed from the UI). The page header/subtitle,
    // the compact 3-step flow strip and the logged-out signup gate. If a pivot
    // string is un-routed, add it here so the leak stays caught.
    'strava.pageHeader',
    'strava.pageSubtitle',
    'strava.pageSubtitleStravaLink',
    'strava.stravaHelpSummary',
    'strava.garminHelpSummary',
    'strava.garminHelpBody',
    'strava.garminHelpLink',
    'strava.flow.step1',
    'strava.flow.step2',
    'strava.flow.step3',
    'strava.signupToContribute',
    'strava.signupToContributeSub',
    'strava.communityMap',
    'strava.contributeHeatmap',
    'strava.canLeavePage',
    'strava.importProgress',
    'strava.resultImported',
    'strava.resultSkipped',
    'strava.resultFailed',
    'strava.compatible',
    'strava.chooseFiles',
    // Two-block hero card (2026-07): archive block (sport read from
    // activities.csv, no chips) vs loose GPX/FIT block (user states the sport).
    'strava.blockArchiveTitle',
    'strava.blockFilesTitle',
    'strava.chooseArchive',
    'strava.archiveHelpLink',
    'strava.sportFromArchive',
    'strava.sportConfirmTitle',
    'strava.importViaStrava',
    'strava.queued',
    'strava.queuedOther',
    'strava.queuedWait',
    'strava.readingHistory',
    'strava.statNew',
    'strava.statError',
    'strava.statImported',
    'strava.startingImport',
    'strava.connectingReading',
    'strava.continuesBackground',
    'strava.gpsHighQuality',
    'strava.importStravaMin',
    'strava.willExportGpx',
    'strava.continueWithStrava',
    // Strava-archive HELP block (help-only after the unified-dropzone refactor —
    // the deposit itself lives once, in the hero ContributionDropzone; this
    // section only explains WHY + HOW to get the archive out of Strava).
    'strava.archive.title',
    'strava.archive.intro',
    // "Why upload a file rather than connect Strava?" mission explainer.
    'strava.archive.whyTitle',
    'strava.archive.whyBody',
    'strava.archive.reassure',
    'strava.archive.howto',
    'strava.archive.s1',
    'strava.archive.s2',
    'strava.archive.s2LinkLabel',
    'strava.archive.s3',
    'strava.archive.s4',
    'strava.archive.helpLink',
    'strava.archive.exportPageLink',
    'strava.archive.dropOnHero',
    // The ONE ODbL consent + upload progress labels back the hero dropzone.
    'strava.archive.consentLabel',
    'strava.archive.consentRequired',
    'strava.archive.preparing',
    'strava.archive.uploadingPct',
    'strava.archive.finalizing',
    'strava.archive.acceptedQueued',
    'strava.archive.failed',
    'common.appName',
  ];

  it.each(wizardKeys)('%s is defined in fr and en', (key) => {
    expect(fr[key], `fr is missing ${key}`).toBeTruthy();
    expect(en[key], `en is missing ${key}`).toBeTruthy();
  });
});

describe('Admin dashboard — every card / stat / column string is routed (no French leak)', () => {
  // Keys backing the strings that were hardcoded in French in app/admin/page.tsx
  // before the deploy+robustness hardening PR. #440 routed the header + users
  // table; this block pins the rest (cards, stat labels, table columns, sport
  // labels). If any is un-routed again, add it here so the leak stays caught.
  const adminKeys = [
    'admin.apiRevision',
    'admin.card.users', 'admin.card.activities', 'admin.card.totalDistance',
    'admin.card.engagement', 'admin.card.provenance', 'admin.card.bySport',
    'admin.card.routes', 'admin.card.heatmap', 'admin.card.graphHealth',
    'admin.card.network', 'admin.card.database', 'admin.card.recentRoutes',
    'admin.card.recentActivities', 'admin.card.recentJobs', 'admin.card.stravaSync',
    'admin.stat.signedUp', 'admin.stat.importedTraces', 'admin.stat.cumulativeDistance',
    'admin.stat.cumulativeElevation', 'admin.stat.signups7d', 'admin.stat.signups30d',
    'admin.stat.active7d', 'admin.stat.active30d', 'admin.stat.stravaConnected',
    'admin.stat.total', 'admin.stat.edges', 'admin.stat.cells', 'admin.stat.maxContributors',
    'admin.stat.pctOnOsm', 'admin.stat.pctGridFallback', 'admin.stat.vertices',
    'admin.stat.deadEnds', 'admin.stat.pctDeadEnds', 'admin.stat.dfciTracks',
    'admin.stat.markedTrails', 'admin.stat.size', 'admin.stat.connected',
    'admin.stat.delay30d', 'admin.stat.tokenExpired', 'admin.provider.gpxUpload',
    'admin.col.name', 'admin.col.author', 'admin.col.sport', 'admin.col.visibility',
    'admin.col.distance', 'admin.col.elevation', 'admin.col.created', 'admin.col.source',
    'admin.col.imported', 'admin.col.job', 'admin.col.execution', 'admin.col.status',
    'admin.col.start', 'admin.col.duration', 'admin.col.athlete', 'admin.col.lastSync',
    'admin.col.delay', 'admin.col.failures',
    'admin.sport.road', 'admin.sport.gravel', 'admin.sport.mtb',
    'admin.sport.offroad', 'admin.sport.running',
    // Archives (contributions) card — pending_archives queue observability.
    'admin.archives.title', 'admin.archives.empty',
    'admin.archives.status.awaiting_upload', 'admin.archives.status.uploaded',
    'admin.archives.status.processing', 'admin.archives.status.done',
    'admin.archives.status.failed',
    'admin.archives.col.user', 'admin.archives.col.status',
    'admin.archives.col.progress', 'admin.archives.col.error',
    'admin.archives.col.updated',
    'admin.archives.attempts', 'admin.archives.members',
  ];

  it.each(adminKeys)('%s is defined in fr and en', (key) => {
    expect(fr[key], `fr is missing ${key}`).toBeTruthy();
    expect(en[key], `en is missing ${key}`).toBeTruthy();
  });
});

describe('Home hero + sections — every routed string exists in both locales', () => {
  // Keys backing strings that were HARDCODED French literals on the home page
  // (hero overlay, "how it works", routing factors, philosophy footer). Those
  // literals never followed the resolved locale, so an English visitor saw a
  // half-FR / half-EN page (the t()'d tagline/subtitle in English, the
  // hardcoded CTA/badges in French). Routing them through t() fixes the mix.
  // If any is un-routed again, add it here so the leak stays caught.
  const homeKeys = [
    'home.hero.tagline', 'home.hero.subtitle',
    'home.hero.legendLow', 'home.hero.legendHigh',
    'home.hero.exploreMap', 'home.hero.exploreMapNoAccount', 'home.hero.contribute',
    'home.hero.connected', 'home.hero.logout', 'home.hero.join', 'home.hero.joinSub', 'home.hero.disconnected',
    'home.onboarding.ctaTitle', 'home.onboarding.cta', 'home.onboarding.ctaSub',
    'home.badge.anonymized', 'home.badge.openSource',
    'home.section.howItWorks', 'home.section.howItWorksSub',
    'home.step1.title', 'home.step1.desc',
    'home.step2.title', 'home.step2.desc', 'home.step2.strava', 'home.step2.garmin',
    'home.step3.title', 'home.step3.desc',
    'home.calque.title', 'home.calque.intro',
    'home.trails.title', 'home.philosophy.title', 'home.philosophy.method',
    'home.footer.license',
  ];

  it.each(homeKeys)('%s is defined in fr and en', (key) => {
    expect(fr[key], `fr is missing ${key}`).toBeTruthy();
    expect(en[key], `en is missing ${key}`).toBeTruthy();
  });
});

describe('Home page source — no hardcoded FR copy leaked back into the component', () => {
  // Read the REAL component source and assert the French literals that caused
  // the mixed-language hero are gone from the JSX (they must live in the
  // dictionaries and render via t()). This fails on the pre-fix code and passes
  // on the fix — a genuine non-regression guard, not an inline mirror.
  const here = dirname(fileURLToPath(import.meta.url));
  const pageSrc = readFileSync(resolve(here, '../../app/page.tsx'), 'utf8')
    // strip block comments (incl. `{/* … */}` JSX comments) so a French phrase
    // that legitimately remains in a code comment is not a false positive.
    .replace(/\/\*[\s\S]*?\*\//g, '');

  // Keys whose FR copy once leaked into the component as hardcoded literals.
  // The guarded phrase is DERIVED from fr.ts (not repeated here), so a copy
  // rework self-syncs instead of forcing a test edit — and deleting the row is
  // never the cheap way out of a red test.
  const routedKeys = [
    'home.hero.exploreMap',
    'home.hero.join',
    'home.hero.logout',
    'home.hero.disconnected',
    'home.badge.anonymized',
    'home.section.howItWorks',
    'home.section.howItWorksSub',
    'home.step1.title',
    'home.trails.title',
    'home.philosophy.title',
    'home.philosophy.method',
  ];

  it.each(routedKeys)('the fr copy of %s is not hardcoded in app/page.tsx', (key) => {
    const copy = fr[key];
    expect(copy, `fr[${key}] should exist — the routed copy lives there`).toBeTruthy();
    expect(
      pageSrc.includes(copy),
      `fr copy of ${key} ("${copy}") is hardcoded in app/page.tsx — route it through t()`,
    ).toBe(false);
  });
});

describe('Strava-archive "why" explainer — mission copy is routed, not hardcoded', () => {
  // The explainer is user-facing MISSION copy. It must render via t() so both
  // locales stay in sync (the mixed-FR/EN class of bug fixed in #452). Read the
  // REAL component source: assert the FR phrases are NOT inline literals and DO
  // live in the fr dictionary. Fails on a hardcoded regression, passes on the
  // routed version — a genuine guard, not an inline mirror.
  const here = dirname(fileURLToPath(import.meta.url));
  const componentSrc = readFileSync(
    resolve(here, '../../components/StravaArchiveImport.tsx'),
    'utf8',
  ).replace(/\/\*[\s\S]*?\*\//g, '');

  const routedPhrases: Array<[string, string]> = [
    ['Pourquoi déposer un fichier', 'strava.archive.whyTitle'],
    ["C'est tout l'esprit de Chemins Communs", 'strava.archive.whyBody'],
  ];

  it.each(routedPhrases)('"%s" is not hardcoded in StravaArchiveImport.tsx', (phrase) => {
    expect(
      componentSrc.includes(phrase),
      `"${phrase}" is hardcoded in StravaArchiveImport.tsx — route it through t()`,
    ).toBe(false);
  });

  it.each(routedPhrases)('"%s" lives in the fr dictionary instead', (phrase, key) => {
    expect(fr[key], `fr[${key}] should carry the routed copy`).toContain(phrase);
  });
});

describe('Map surface — every routed string exists in both locales', () => {
  // Keys backing strings that were HARDCODED (mostly French) literals across the
  // map surface (toolbar, layer/basemap pickers, import modals, route editor,
  // panels, popups, toasts, import flow). Those literals never followed the
  // resolved locale → with EN selected the toolbar showed a FR/EN mix
  // ("Layers"/"Search for a place…"/"Menu" in EN next to "Fond de carte"/"Tracer"
  // in FR). Routing them through t() fixes the mix. If any is un-routed again,
  // add it here so the leak stays caught. (fix/map-i18n-consolidation)
  const mapKeys = [
    // the visible offenders called out on the toolbar
    'map.layers', 'map.menuButton', 'placeSearch.placeholder',
    // toolbar
    'map.toolbar.heatmapLayer', 'map.offroadCombined', 'map.toolbar.timeAll',
    'map.toolbar.moreLayers', 'map.toolbar.cellCoverage', 'map.toolbar.hillshade',
    'map.toolbar.stravaPhotos', 'map.toolbar.waymarkedHeader', 'map.toolbar.basemap',
    'map.toolbar.exportBtn', 'map.toolbar.exportTitle',
    'map.toolbar.exploreModeOn', 'map.toolbar.exploreModeOff',
    'map.dfciTracks', 'map.dfciTracksCount',
    // basemap + waymarked labels
    'map.basemap.label.bright', 'map.basemap.label.dark', 'map.basemap.label.satellite',
    'map.basemap.title.dark', 'map.waymarked.hiking', 'map.waymarked.cycling', 'map.waymarked.mtb',
    // import launcher (thin signpost → /strava; the in-map upload form + the
    // Garmin/Komoot export tutorials were removed in the unified-dropzone pass)
    'map.importTitle', 'map.import.launcherBody', 'map.import.launcherCta',
    'map.howRouting.signalsIntro', 'map.howRouting.learnMore', 'map.lightbox.photo',
    // NOTE: the in-app WASM route editor + route-mode toolbar were decommissioned
    // in #518. Their keys (map.editor.*, map.method.*/methodDesc.*, map.waypoints.*,
    // map.route.*, map.toolbar.routeMode*/drafts, routing status/toasts, warming
    // messages, the method./explain./badge. routing labels) were deleted from both
    // dictionaries — do not re-pin them here.
    // page: default names, overlays, popups
    'map.activityDefaultName',
    'map.loadingCommunityTraces', 'map.loadingHeatmap',
    'map.gpxDropHint', 'map.addAnnotation', 'map.segmentHeatmap', 'map.contributors',
    'map.passes', 'map.directionOfTravel', 'map.traceFromHere', 'map.sportFilter.all',
    'map.sportFilter.running', 'map.comparison', 'map.openOnPanoramax', 'map.linkCopied',
    // panels
    'map.legend.popularity', 'map.legend.offroad',
    'map.activity.distance', 'map.activity.viewDetails', 'map.photos.stripLabel', 'map.photos.stravaPhoto',
  ];

  it.each(mapKeys)('%s is defined in fr and en', (key) => {
    expect(fr[key], `fr is missing ${key}`).toBeTruthy();
    expect(en[key], `en is missing ${key}`).toBeTruthy();
  });
});

describe('Map component sources — no hardcoded FR copy leaked back into the JSX', () => {
  // Read the REAL component sources and assert the French literals that caused
  // the mixed-language toolbar/map are gone from the JSX (they must live in the
  // dictionaries and render via t()). Block/JSX comments are stripped so a
  // French phrase that legitimately remains in a code comment is not a false
  // positive. Fails on the pre-fix code, passes on the fix.
  const here = dirname(fileURLToPath(import.meta.url));
  const strip = (rel: string) =>
    readFileSync(resolve(here, rel), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');

  const toolbarSrc = strip('../../components/map/MapToolbar.tsx');
  const toolbarPhrases: Array<[string, string]> = [
    ['Fond de carte', 'map.toolbar.basemap'],
    ['Plus de calques', 'map.toolbar.moreLayers'],
    ['Sentiers balisés', 'map.toolbar.waymarkedHeader'],
    ['Heatmap communautaire', 'map.toolbar.heatmapLayer'],
    ['🔍 Explorer la heatmap', 'map.toolbar.exploreModeOff'],
    ['⬇️ Exporter', 'map.toolbar.exportBtn'],
    ['Couverture cellules', 'map.toolbar.cellCoverage'],
  ];
  it.each(toolbarPhrases)('MapToolbar: "%s" is not hardcoded', (phrase) => {
    expect(toolbarSrc.includes(phrase), `"${phrase}" is still hardcoded in MapToolbar.tsx — route it through t()`).toBe(false);
  });
  it.each(toolbarPhrases)('MapToolbar: "%s" lives in the fr dictionary', (phrase, key) => {
    expect(fr[key], `fr[${key}] should carry "${phrase}"`).toContain(phrase);
  });

  const pageSrc = strip('../../app/map/page.tsx');
  const pagePhrases: Array<[string, string]> = [
    ['Segment heatmap', 'map.segmentHeatmap'],
    ['Ajouter une annotation', 'map.addAnnotation'],
    ['Comparaison', 'map.comparison'],
    ['Lien copié', 'map.linkCopied'],
    ['Glisser un fichier .gpx', 'map.gpxDropHint'],
    ['Chargement de la heatmap', 'map.loadingHeatmap'],
    ['Sens de passage', 'map.directionOfTravel'],
  ];
  it.each(pagePhrases)('map/page: "%s" is not hardcoded', (phrase) => {
    expect(pageSrc.includes(phrase), `"${phrase}" is still hardcoded in app/map/page.tsx — route it through t()`).toBe(false);
  });
  it.each(pagePhrases)('map/page: "%s" lives in the fr dictionary', (phrase, key) => {
    expect(fr[key], `fr[${key}] should carry "${phrase}"`).toContain(phrase);
  });
});
