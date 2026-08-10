import { defineConfig } from 'vitest/config';
import path from 'path';

export default defineConfig({
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './'),
    },
  },
  test: {
    // Only include tests that use vitest API (not legacy tsx-based tests)
    include: [
      'lib/__tests__/map-utils.test.ts',
      'lib/__tests__/gpx-utils.test.ts',
      'lib/__tests__/format.test.ts',
      'lib/__tests__/folder-zip.test.ts',
      'lib/__tests__/i18n-parity.test.ts',
      'lib/__tests__/strava-archive-consent.test.ts',
      'lib/__tests__/strava-archive-upload.test.ts',
      'lib/__tests__/strava-upload-summary.test.ts',
      'lib/__tests__/upload-progress.test.ts',
      'lib/__tests__/community-stats.test.ts',
      'lib/__tests__/community-heatmap-layers.test.ts',
      'lib/__tests__/admin-metrics.test.ts',
      'lib/__tests__/notification-display.test.ts',
      'lib/__tests__/email-auth.test.ts',
      'lib/__tests__/community-pmtiles-url.test.ts',
      'lib/__tests__/env-url.test.ts',
      'lib/__tests__/cdn-pointer.test.ts',
    ],
    environment: 'node',
    globals: false,
  },
});
