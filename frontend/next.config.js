/** @type {import('next').NextConfig} */
const isGCS = process.env.DEPLOY_TARGET === 'gcs';
const GCS_BASE_PATH = '/common-trails-frontend';

const nextConfig = {
  output: 'export',
  images: {
    unoptimized: true,
  },
  // Ship sourcemaps to the browser in prod so React errors (Minified
  // React error #418 hydration mismatch, etc.) translate from
  // minified `rK / n / sp / sc` symbols to actual file:line + the
  // un-minified message when the user opens DevTools. AGPL project,
  // source is public on GitHub anyway — no leakage concern. Cost is
  // ~3-5 MB of additional asset weight on the CDN per deploy, only
  // fetched when DevTools is open.
  productionBrowserSourceMaps: true,
  ...(isGCS && {
    basePath: GCS_BASE_PATH,
  }),
  env: {
    NEXT_PUBLIC_BASE_PATH: isGCS ? GCS_BASE_PATH : '',
  },
};

module.exports = nextConfig;
