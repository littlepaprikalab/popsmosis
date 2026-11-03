// Renders every raster icon from src/app/icon.svg, the one source of the tab icon.
// Run it after changing that SVG: `npm --workspace web run icons`.
//
//   src/app/favicon.ico      16, 32 and 48 px, for browsers that skip the SVG
//   src/app/apple-icon.png   180 px, the iPhone and iPad home screen
//   public/icons/*.png       192 and 512 px, the Android home screen (src/app/manifest.ts)
//
// iOS and Android's "maskable" icons get a full-bleed square with no rounded corners:
// the device rounds the corners itself, and transparent corners would turn black.
import { chromium } from '@playwright/test';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const web = join(dirname(fileURLToPath(import.meta.url)), '..');
const tile = readFileSync(join(web, 'src/app/icon.svg'), 'utf8');
const square = tile.replace(/ rx="[\d.]+"/, '');
if (square === tile) throw new Error('icon.svg: expected the tile <rect> to have an rx to remove');

// The same Chromium as the browser tests (playwright.config.ts): CHROMIUM_PATH can point at
// a build other than the one @playwright/test pins, and a cloud session's own is used when
// the pinned build is missing.
const CLOUD_CHROMIUM = '/opt/pw-browsers/chromium';
let pinned = false;
try {
  pinned = existsSync(chromium.executablePath());
} catch {
  // no build for this platform
}
const executablePath = process.env.CHROMIUM_PATH || (!pinned && existsSync(CLOUD_CHROMIUM) ? CLOUD_CHROMIUM : undefined);
const browser = await chromium.launch({ executablePath });
const page = await browser.newPage();

async function png(svg, size) {
  await page.setViewportSize({ width: size, height: size });
  const src = `data:image/svg+xml;base64,${Buffer.from(svg).toString('base64')}`;
  await page.setContent(`<style>html,body{margin:0;background:transparent}</style><img src="${src}" width="${size}" height="${size}" style="display:block">`);
  await page.waitForFunction(() => document.images[0].complete && document.images[0].naturalWidth > 0);
  return page.screenshot({ omitBackground: true, clip: { x: 0, y: 0, width: size, height: size } });
}

/** An .ico holding PNG images, which every browser that still asks for one can read. */
function ico(images) {
  const header = Buffer.alloc(6);
  header.writeUInt16LE(1, 2); // type: icon
  header.writeUInt16LE(images.length, 4);
  let offset = 6 + 16 * images.length;
  const entries = images.map(({ size, data }) => {
    const e = Buffer.alloc(16);
    e.writeUInt8(size % 256, 0); // 0 means 256
    e.writeUInt8(size % 256, 1);
    e.writeUInt16LE(1, 4); // color planes
    e.writeUInt16LE(32, 6); // bits per pixel
    e.writeUInt32LE(data.length, 8);
    e.writeUInt32LE(offset, 12);
    offset += data.length;
    return e;
  });
  return Buffer.concat([header, ...entries, ...images.map((i) => i.data)]);
}

const out = (path, data) => {
  mkdirSync(dirname(join(web, path)), { recursive: true });
  writeFileSync(join(web, path), data);
  console.log(`${path} (${data.length} bytes)`);
};

// One at a time: png() resizes and refills the one page it draws on.
const frames = [];
for (const size of [16, 32, 48]) frames.push({ size, data: await png(tile, size) });
out('src/app/favicon.ico', ico(frames));
out('src/app/apple-icon.png', await png(square, 180));
out('public/icons/icon-192.png', await png(tile, 192));
out('public/icons/icon-512.png', await png(tile, 512));
out('public/icons/icon-maskable-512.png', await png(square, 512));

await browser.close();
