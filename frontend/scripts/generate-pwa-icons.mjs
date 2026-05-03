#!/usr/bin/env node
/**
 * Render every PWA / favicon / Apple-touch icon size from the single
 * source SVG at public/icons/icon-source.svg.
 *
 * Run with:
 *
 *   npm run generate:pwa-icons
 *
 * Outputs land in public/ (favicon variants and apple-touch-icon)
 * and public/icons/ (manifest icons including the maskable variant).
 *
 * The maskable icon is rendered at 80% of its canvas, centered, so
 * Android adaptive-icon shape masks can crop a circle/rounded square
 * without slicing the visible mark. Per W3C maskable-icon spec the
 * minimum safe-zone padding is 10%; we use 20% to be conservative.
 */

import { promises as fs } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import sharp from "sharp";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..");
const SOURCE_SVG = path.join(REPO_ROOT, "public", "icons", "icon-source.svg");
const PUBLIC = path.join(REPO_ROOT, "public");
const ICONS = path.join(PUBLIC, "icons");
const BRAND_BG = "#F7F0EE";

async function readSourceBuffer() {
  return fs.readFile(SOURCE_SVG);
}

async function renderSquare(svg, size, outPath) {
  await sharp(svg, { density: 384 })
    .resize(size, size, { fit: "contain", background: BRAND_BG })
    .png({ compressionLevel: 9 })
    .toFile(outPath);
  console.log(`wrote ${path.relative(REPO_ROOT, outPath)} (${size}x${size})`);
}

async function renderMaskable(svg, size, outPath) {
  // 20% safe-area padding. The visible mark is rendered into 80% of
  // the canvas, then composited onto a flat brand-colored square so
  // adaptive-icon masks have something to crop.
  const inner = Math.round(size * 0.8);
  const innerBuffer = await sharp(svg, { density: 384 })
    .resize(inner, inner, { fit: "contain", background: BRAND_BG })
    .png()
    .toBuffer();
  await sharp({
    create: {
      width: size,
      height: size,
      channels: 3,
      background: BRAND_BG,
    },
  })
    .composite([
      {
        input: innerBuffer,
        top: Math.round((size - inner) / 2),
        left: Math.round((size - inner) / 2),
      },
    ])
    .png({ compressionLevel: 9 })
    .toFile(outPath);
  console.log(`wrote ${path.relative(REPO_ROOT, outPath)} (${size}x${size}, maskable)`);
}

async function renderFaviconIco(svg) {
  // .ico is multi-image; sharp only ships the embedded sizes one at
  // a time, so we render the 32px PNG and write it as the .ico.
  // Modern browsers happily read 32x32 PNGs out of an .ico container.
  const png32 = await sharp(svg, { density: 256 })
    .resize(32, 32, { fit: "contain", background: BRAND_BG })
    .png()
    .toBuffer();
  await fs.writeFile(path.join(PUBLIC, "favicon.ico"), png32);
  console.log("wrote public/favicon.ico (32x32 PNG inside .ico)");
}

async function main() {
  const svg = await readSourceBuffer();
  await fs.mkdir(ICONS, { recursive: true });

  await renderSquare(svg, 192, path.join(ICONS, "icon-192.png"));
  await renderSquare(svg, 512, path.join(ICONS, "icon-512.png"));
  await renderMaskable(svg, 512, path.join(ICONS, "icon-maskable-512.png"));
  await renderSquare(svg, 180, path.join(PUBLIC, "apple-touch-icon.png"));
  await renderSquare(svg, 16, path.join(PUBLIC, "favicon-16x16.png"));
  await renderSquare(svg, 32, path.join(PUBLIC, "favicon-32x32.png"));
  await renderFaviconIco(svg);
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
