// Builds the unpacked extension into dist/. The manifest is written from
// src/config.ts so the name, version and allowed origins live in one place.
import { build } from 'esbuild';
import { cp, mkdir, rm, writeFile } from 'node:fs/promises';
import { BRAND, EXTERNALLY_CONNECTABLE, LINKEDIN, LOCAL_SERVER_HOSTS, X } from './src/config.ts';

const OUT = 'dist';

const manifest = {
  manifest_version: 3,
  name: BRAND.extensionName,
  version: BRAND.version,
  description: BRAND.description,
  minimum_chrome_version: '111', // content scripts in the page's own world
  permissions: ['storage', 'alarms'],
  host_permissions: [`${X.origin}/*`, `${LINKEDIN.origin}/*`, ...LOCAL_SERVER_HOSTS],
  // Asked for in the popup only when the server URL is not on this computer.
  optional_host_permissions: ['https://*/*', 'http://*/*'],
  background: { service_worker: 'background.js', type: 'module' },
  action: { default_title: BRAND.extensionName, default_popup: 'popup.html' },
  content_scripts: [
    { matches: [`${X.origin}/*`], js: ['x-inpage.js'], run_at: 'document_start', world: 'MAIN' },
    { matches: [`${X.origin}/*`], js: ['x-content.js'], run_at: 'document_start' },
    { matches: [`${LINKEDIN.origin}/*`], js: ['linkedin-content.js'], run_at: 'document_idle' },
  ],
  externally_connectable: { matches: EXTERNALLY_CONNECTABLE },
};

await rm(OUT, { recursive: true, force: true });
await mkdir(OUT, { recursive: true });

const common = { bundle: true, target: 'chrome111', logLevel: 'info' };
await build({ ...common, format: 'esm', entryPoints: { background: 'src/background.ts' }, outdir: OUT });
// Content scripts and the popup are classic scripts, so each is one self-contained file.
await build({
  ...common,
  format: 'iife',
  entryPoints: {
    'x-inpage': 'src/x/inpage.ts',
    'x-content': 'src/x/content.ts',
    'linkedin-content': 'src/linkedin/content.ts',
    popup: 'src/popup/popup.ts',
  },
  outdir: OUT,
});

await cp('src/popup/popup.html', `${OUT}/popup.html`);
await cp('NOTICE', `${OUT}/NOTICE`);
await writeFile(`${OUT}/manifest.json`, JSON.stringify(manifest, null, 2) + '\n');
console.log(`Built ${BRAND.extensionName} ${BRAND.version} into ${OUT}/`);
