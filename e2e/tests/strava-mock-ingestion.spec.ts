/**
 * CHEMINS COMMUNS — Strava-connect proof via the TEST_MODE stubs.
 *
 * API-level spec (request contexts only, no browser page) driving the REAL
 * Strava endpoints end-to-end as far as the stubs allow. CI-safe: relies on
 * TEST_MODE=true (local docker-compose default AND ci.yml), no external call.
 *
 * ── What TEST_MODE stubs out (verified in backend/, 2026-07) ──────────────
 *
 * `integrations_strava.py`
 *   * `/connect` + `/login`: skip the Strava authorize page, 307 straight to
 *     `/callback?code=stub_code_test&state=<signed JWT>`.
 *   * `/callback`: skips the token exchange (the MOCKED seam) but does the
 *     REAL DB work — upserts an IntegrationAccount row with
 *     external_user_id="strava_stub_user_42" + stub tokens.
 *   * `/import_all` + `_run_strava_import`: FULLY stubbed — the job is marked
 *     COMPLETED without fetching or ingesting anything, so no activity can
 *     land through the bulk-import path in TEST_MODE.
 *
 * `internal_strava_webhook.py`
 *   * `_ingest_strava_activity` (create/update events): returns a stub dict
 *     BEFORE `classify_strava_sport_or_skip` + `ingest_activities_bulk` run.
 *     The Strava activity fetch is the MOCKED seam — a webhook `create`
 *     cannot land an activity in TEST_MODE.
 *   * The ATHLETE DEAUTHORIZE and activity DELETE branches are NOT stubbed:
 *     they run real DB queries/deletes even in TEST_MODE.
 *   * OIDC verification is skipped in TEST_MODE (no Cloud Tasks locally).
 *
 * `cloud_tasks.enqueue_strava_webhook_event`
 *   * No CLOUD_TASKS_STRAVA_WEBHOOK_QUEUE configured locally/CI → the public
 *     webhook handler runs the worker INLINE (`process_strava_webhook_event`),
 *     so the full public-endpoint → allow-list → worker chain is exercised
 *     in-process without Cloud Tasks or OIDC.
 *
 * ── Deepest REAL seams proven here ────────────────────────────────────────
 *
 *   1. OAuth connect chain: /connect → 307 → /callback (stub code) → REAL
 *      IntegrationAccount row → /status connected=true.
 *   2. Public webhook receiver: create events for a connected owner ack "ok"
 *      (chain stops at the TEST_MODE activity-fetch stub); unknown owners are
 *      dropped by the REAL DB allow-list ("unknown-owner"); athlete
 *      deauthorize runs the REAL teardown (account deleted → /status
 *      connected=false). Handshake never leaks the challenge unverified.
 *   3. Sport classification (#431 SSOT `strava_utils.classify_sport`): the
 *      webhook + bulk paths are stubbed BEFORE classification in TEST_MODE,
 *      so the deepest real classification seam reachable over HTTP is the
 *      Strava bulk-export archive path — `/imports/files` with a ZIP whose
 *      `activities.csv` rows drive `parse_strava_activities_csv` →
 *      `classify_sport(activity_type, activity_name)` → real ingest →
 *      `/me/activities`. That is the SAME single-source-of-truth classifier
 *      the webhook worker and bulk import call with (sport_type, name), so a
 *      regression in the "Ride" + gravel/VTT name refinement OR in the
 *      "Gravel Ride" type mapping fails this spec.
 */
import { expect, request, test } from '@playwright/test';

const API_URL = process.env.API_URL || 'http://localhost:8787';

/** Register a fresh user via API; return JWT + user_id. */
async function registerAndLogin(
  apiContext: Awaited<ReturnType<typeof request.newContext>>,
): Promise<{ token: string; userId: string; email: string }> {
  const uid = `${Date.now()}_${Math.floor(Math.random() * 100000)}`;
  const email = `strava_mock_${uid}@example.com`;
  const regResp = await apiContext.post(`${API_URL}/auth/register`, {
    data: { email, password: 'testpassword123', username: `stravamock_${uid}` },
  });
  expect(regResp.status()).toBe(201);
  const data = await regResp.json();
  return { token: data.access_token, userId: data.user_id, email };
}

// The fixed Strava athlete id the TEST_MODE callback binds
// (integrations_strava.py `stub_strava_id`). Webhook events for this
// owner_id pass the public handler's DB allow-list.
const STUB_STRAVA_ID = 'strava_stub_user_42';

// ── 1 + 2. OAuth connect + webhook chain ──────────────────────────────────
//
// Single test (not a serial describe): every step shares the global
// STUB_STRAVA_ID identity, and `fullyParallel: true` would let separate
// tests interleave — the deauthorize teardown of one test could delete the
// account another test just connected.
test.describe('Strava connect + webhook chain (TEST_MODE stubs)', () => {
  test('stubbed OAuth connect → status connected → webhook create/delete acked → deauthorize tears down for real', async () => {
    const apiContext = await request.newContext();
    const { token } = await registerAndLogin(apiContext);
    const auth = { Authorization: `Bearer ${token}` };

    // ① /connect redirects to the stubbed callback (no Strava page)
    const connectResp = await apiContext.get(
      `${API_URL}/integrations/strava/connect`,
      { headers: auth, maxRedirects: 0 },
    );
    expect(connectResp.status()).toBe(307);
    const callbackLocation = connectResp.headers()['location'];

    // Runtime TEST_MODE probe. A dev backend configured with REAL Strava
    // credentials (TEST_MODE=false, e.g. Paul's local .env) redirects to
    // strava.com — driving the rest of this chain there would make real
    // OAuth/API calls with stub tokens. The stubbed chain is what this
    // spec proves; it runs in CI (TEST_MODE=true) and on any TEST_MODE
    // backend. To exercise it locally: restart the backend with
    // TEST_MODE=true (or run a second backend container) and re-run.
    test.skip(
      callbackLocation.includes('strava.com'),
      'backend is not in TEST_MODE (real Strava OAuth configured) — stubbed chain skipped',
    );

    expect(callbackLocation).toContain('/integrations/strava/callback');
    expect(callbackLocation).toContain('code=stub_code_test');
    expect(callbackLocation).toContain('state='); // signed-JWT CSRF state round-trips

    // ② The callback binds a REAL IntegrationAccount (stub token exchange)
    const callbackUrl = new URL(callbackLocation, API_URL).toString();
    const callbackResp = await apiContext.get(callbackUrl, { maxRedirects: 0 });
    expect(callbackResp.status()).toBe(307);
    expect(callbackResp.headers()['location']).toContain('status=connected');

    // ③ /status reflects the connected account, with the stub athlete id
    //    the webhook allow-list will match on.
    const statusResp = await apiContext.get(
      `${API_URL}/integrations/strava/status`, { headers: auth },
    );
    expect(statusResp.status()).toBe(200);
    const status = await statusResp.json();
    expect(status.connected).toBe(true);
    expect(status.athlete_id).toBe(STUB_STRAVA_ID);

    // ④ Webhook `create` for the connected owner: passes the REAL DB
    //    allow-list, runs the worker INLINE (no Cloud Tasks configured),
    //    and acks "ok". In TEST_MODE the worker stops at the stubbed
    //    Strava activity fetch — the MOCKED seam — so no activity lands;
    //    what this pins is the ack-fast public layer + owner allow-list +
    //    enqueue-inline chain returning 200 to Strava.
    const createEvent = {
      object_type: 'activity',
      object_id: 4242424242,
      aspect_type: 'create',
      owner_id: STUB_STRAVA_ID,
      subscription_id: 1,
      event_time: Math.floor(Date.now() / 1000),
      updates: {},
    };
    const createResp = await apiContext.post(
      `${API_URL}/integrations/strava/webhook`, { data: createEvent },
    );
    expect(createResp.status()).toBe(200);
    expect(await createResp.text()).toBe('ok');

    // ⑤ Unknown owner → dropped by the allow-list, still 200 so Strava
    //    doesn't retry a forged event.
    const unknownResp = await apiContext.post(
      `${API_URL}/integrations/strava/webhook`,
      { data: { ...createEvent, owner_id: `e2e_unknown_${Date.now()}` } },
    );
    expect(unknownResp.status()).toBe(200);
    expect(await unknownResp.text()).toBe('unknown-owner');

    // ⑥ Activity `delete` for an activity we never imported: the worker's
    //    delete branch is REAL even in TEST_MODE (DB lookup) and must be a
    //    silent idempotent no-op → 200 "ok".
    const deleteResp = await apiContext.post(
      `${API_URL}/integrations/strava/webhook`,
      { data: { ...createEvent, aspect_type: 'delete', object_id: 999999999 } },
    );
    expect(deleteResp.status()).toBe(200);
    expect(await deleteResp.text()).toBe('ok');

    // ⑦ Athlete DEAUTHORIZE: the worker branch is NOT stubbed — it deletes
    //    the IntegrationAccount for real. Proven end-to-end: public POST →
    //    inline worker → DB teardown → /status flips to disconnected.
    //    Bounded retry: each deauthorize removes ONE account matching the
    //    stub athlete id (`.first()`), so a stray row left by an earlier
    //    crashed run converges within a few posts.
    const deauthEvent = {
      object_type: 'athlete',
      object_id: 42,
      aspect_type: 'update',
      owner_id: STUB_STRAVA_ID,
      subscription_id: 1,
      event_time: Math.floor(Date.now() / 1000),
      updates: { authorized: 'false' },
    };
    let disconnected = false;
    for (let attempt = 0; attempt < 5 && !disconnected; attempt++) {
      const deauthResp = await apiContext.post(
        `${API_URL}/integrations/strava/webhook`, { data: deauthEvent },
      );
      expect(deauthResp.status()).toBe(200);
      const after = await apiContext.get(
        `${API_URL}/integrations/strava/status`, { headers: auth },
      );
      disconnected = (await after.json()).connected === false;
    }
    expect(disconnected, 'deauthorize webhook must tear down the account').toBe(true);

    await apiContext.dispose();
  });

  test('webhook handshake never echoes the challenge to an unverified caller', async () => {
    // STRAVA_WEBHOOK_VERIFY_TOKEN is unset locally + in CI → 503 (fails
    // loudly on misconfiguration). If someone configures it, a wrong
    // verify_token must yield 403. Either way the hub.challenge must NOT
    // be echoed back.
    const apiContext = await request.newContext();
    const challenge = `e2e-challenge-${Date.now()}`;
    const resp = await apiContext.get(
      `${API_URL}/integrations/strava/webhook`
      + `?hub.mode=subscribe&hub.verify_token=definitely-wrong&hub.challenge=${challenge}`,
    );
    expect([403, 503]).toContain(resp.status());
    expect(await resp.text()).not.toContain(challenge);
    await apiContext.dispose();
  });
});

// ── 3. Sport classification — #431 SSOT via the Strava bulk-export path ───

/** Minimal GPX with a generic <type> so the CSV hint (not the in-GPX type
 *  and not the form sport) must decide the classification. */
function makeGpx(name: string, baseLat: number, date: string): Buffer {
  const pts = [0, 1, 2, 3].map((i) => {
    const lat = (baseLat + i * 0.001).toFixed(5);
    const lon = (3.87 + i * 0.001).toFixed(5);
    return `      <trkpt lat="${lat}" lon="${lon}"><ele>${50 + i}</ele><time>${date}T08:0${i}:00Z</time></trkpt>`;
  }).join('\n');
  return Buffer.from(
    `<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="StravaGPX" xmlns="http://www.topografix.com/GPX/1/1">
  <trk>
    <name>${name}</name>
    <type>cycling</type>
    <trkseg>
${pts}
    </trkseg>
  </trk>
</gpx>`,
    'utf-8',
  );
}

// ── Dependency-free ZIP builder (STORE method) ────────────────────────────
// e2e has no archiver/jszip dependency; a stored (uncompressed) ZIP is
// ~60 lines and keeps package-lock.json untouched (CI runs node 22 — a
// lock regenerated with a newer local node breaks `npm ci`, see CLAUDE.md).

function crc32(buf: Buffer): number {
  let crc = 0xffffffff;
  for (const byte of buf) {
    crc ^= byte;
    for (let k = 0; k < 8; k++) {
      crc = crc & 1 ? 0xedb88320 ^ (crc >>> 1) : crc >>> 1;
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function buildStoredZip(entries: { name: string; data: Buffer }[]): Buffer {
  const localParts: Buffer[] = [];
  const centralParts: Buffer[] = [];
  let offset = 0;
  const DOS_DATE = ((2024 - 1980) << 9) | (5 << 5) | 1; // 2024-05-01

  for (const { name, data } of entries) {
    const nameBuf = Buffer.from(name, 'utf-8');
    const crc = crc32(data);

    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0); // local file header signature
    local.writeUInt16LE(20, 4);         // version needed
    local.writeUInt16LE(0, 6);          // flags
    local.writeUInt16LE(0, 8);          // method: STORE
    local.writeUInt16LE(0, 10);         // mod time
    local.writeUInt16LE(DOS_DATE, 12);  // mod date
    local.writeUInt32LE(crc, 14);
    local.writeUInt32LE(data.length, 18); // compressed size (= raw, STORE)
    local.writeUInt32LE(data.length, 22); // uncompressed size
    local.writeUInt16LE(nameBuf.length, 26);
    local.writeUInt16LE(0, 28);         // extra length
    localParts.push(local, nameBuf, data);

    const central = Buffer.alloc(46);
    central.writeUInt32LE(0x02014b50, 0); // central directory signature
    central.writeUInt16LE(20, 4);         // version made by
    central.writeUInt16LE(20, 6);         // version needed
    central.writeUInt16LE(0, 8);          // flags
    central.writeUInt16LE(0, 10);         // method: STORE
    central.writeUInt16LE(0, 12);         // mod time
    central.writeUInt16LE(DOS_DATE, 14);  // mod date
    central.writeUInt32LE(crc, 16);
    central.writeUInt32LE(data.length, 20);
    central.writeUInt32LE(data.length, 24);
    central.writeUInt16LE(nameBuf.length, 28);
    // extra(30) / comment(32) / disk#(34) / int-attrs(36) / ext-attrs(38): 0
    central.writeUInt32LE(offset, 42);    // local header offset
    centralParts.push(central, nameBuf);

    offset += 30 + nameBuf.length + data.length;
  }

  const centralSize = centralParts.reduce((s, b) => s + b.length, 0);
  const eocd = Buffer.alloc(22);
  eocd.writeUInt32LE(0x06054b50, 0); // end-of-central-directory signature
  eocd.writeUInt16LE(entries.length, 8);
  eocd.writeUInt16LE(entries.length, 10);
  eocd.writeUInt32LE(centralSize, 12);
  eocd.writeUInt32LE(offset, 16);
  eocd.writeUInt16LE(0, 20);
  return Buffer.concat([...localParts, ...centralParts, eocd]);
}

test.describe('Strava bulk-export sport classification (#431 SSOT)', () => {
  test('activities.csv drives classify_sport: Ride+gravel-name → gravel, GravelRide → gravel, Ride+VTT-name → mtb, plain Ride → road, VirtualRide → skipped', async () => {
    const apiContext = await request.newContext();
    const { token } = await registerAndLogin(apiContext);

    // Names carry the #431 signal; distinct dates dodge the cross-provider
    // date±5min dedup between this user's own activities.
    const rows = [
      { id: 9001, name: 'Sortie gravel du dimanche', type: 'Ride', date: '2024-05-01', lat: 43.60, expected: 'gravel' },
      { id: 9002, name: 'Boucle tranquille', type: 'Gravel Ride', date: '2024-05-02', lat: 43.70, expected: 'gravel' },
      { id: 9003, name: 'Sortie VTT Pic Saint-Loup', type: 'Ride', date: '2024-05-03', lat: 43.80, expected: 'mtb' },
      { id: 9004, name: 'Balade du soir', type: 'Ride', date: '2024-05-04', lat: 43.90, expected: 'road' },
    ];
    const skippedRow = { id: 9005, name: 'Zwift - Watopia', type: 'Virtual Ride', date: '2024-05-05', lat: -11.60 };

    const csvLines = [
      'Activity ID,Activity Date,Activity Name,Activity Type,Filename',
      ...[...rows, skippedRow].map((r) =>
        `${r.id},"${r.date} 08:00:00",${r.name},${r.type},activities/${r.id}.gpx`),
    ];
    const entries = [
      { name: 'activities.csv', data: Buffer.from(csvLines.join('\n') + '\n', 'utf-8') },
      ...[...rows, skippedRow].map((r) => ({
        name: `activities/${r.id}.gpx`,
        data: makeGpx(r.name, r.lat, r.date),
      })),
    ];
    const zip = buildStoredZip(entries);

    // Form sport deliberately 'road': for the refinement rows a regression
    // of the SSOT name-refinement would ALSO classify road → the gravel/mtb
    // assertions below fail. contribute_heatmap=false keeps the shared
    // local heat_edges clean (and skips the slow inline heat compute that
    // TEST_MODE runs synchronously).
    const upload = await apiContext.post(`${API_URL}/imports/files`, {
      headers: { Authorization: `Bearer ${token}` },
      multipart: {
        file: { name: 'strava_export_e2e.zip', mimeType: 'application/zip', buffer: zip },
        sport: 'road',
        contribute_heatmap: 'false',
      },
    });
    expect(upload.status()).toBe(202);
    const body = await upload.json();
    expect(body.imported).toBe(4);
    // The Virtual Ride is skipped (skip-beats-pollute), never falls back
    // to the form sport, and the reason is surfaced.
    expect(body.skipped).toBe(1);
    expect(body.failed).toBe(0);
    expect(
      (body.errors as string[]).some((e) => e.includes('out-of-scope')),
      `expected an out-of-scope skip message, got: ${JSON.stringify(body.errors)}`,
    ).toBe(true);

    // Assert the stored sports through the real read path.
    const meActs = await apiContext.get(`${API_URL}/me/activities?limit=100`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(meActs.status()).toBe(200);
    const geo = await meActs.json();
    type Feat = { properties: { name: string; sport: string } };
    const bySport = new Map<string, string>(
      (geo.features as Feat[]).map((f) => [f.properties.name, f.properties.sport]),
    );

    for (const r of rows) {
      expect(bySport.get(r.name), `sport for "${r.name}" (type=${r.type})`).toBe(r.expected);
    }
    // The Zwift ride must NOT have landed at all.
    expect(bySport.has(skippedRow.name)).toBe(false);

    await apiContext.dispose();
  });
});
