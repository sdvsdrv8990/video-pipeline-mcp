// .claude/skills/studio-web-standards/scripts/devices.mjs — студия глазами устройств.
//
// Собранное дерево открывается в Chromium с профилями телефона, планшета и монитора (ширина,
// плотность пикселей, касание, мобильный user-agent) — это эмуляция устройства, а не другой
// движок: причуды WebKit и Gecko она не показывает. На каждом профиле — снимок всей страницы и
// те же геометрические проверки, что у стенда (`tests/studio_emulation/geometry.spec.ts`), плюс
// прокрутка вбок и цель касания мельче 24 px (WCAG 2.5.8). Замер, а не вердикт гейта.
//
//   npm --prefix studio run build
//   node .claude/skills/studio-web-standards/scripts/devices.mjs [каталог снимков] [дерево]
//
// Каталог по умолчанию — `studio-shots` во временном каталоге системы.
//
// Playwright берётся из дерева эмуляции (`npm --prefix tests/studio_emulation ci`); браузер —
// `PW_CHROMIUM`, иначе предустановленный Chromium среды, иначе тот, что знает Playwright.
import { spawn } from "node:child_process";
import { existsSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../..");
const PORT = 4174;
const TARGET = 24;
const PRESET_CHROMIUM = "/opt/pw-browsers/chromium";

const [out = path.join(os.tmpdir(), "studio-shots"), tree = "studio"] = process.argv.slice(2);
if (!existsSync(path.join(ROOT, tree, "dist"))) {
  console.error(`devices: собранного дерева нет — сначала npm --prefix ${tree} run build`);
  process.exit(2);
}
const require = createRequire(path.join(ROOT, "tests/studio_emulation/package.json"));
const { chromium, devices } = require("@playwright/test");

const PROFILES = [
  ["narrow-320", { viewport: { width: 320, height: 640 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 }],
  ["iphone-14", devices["iPhone 14"]],
  ["pixel-7", devices["Pixel 7"]],
  ["phone-landscape", devices["iPhone 14 landscape"]],
  ["ipad-mini", devices["iPad Mini"]],
  ["laptop-1280", { viewport: { width: 1280, height: 800 } }],
  ["desktop-1920", { viewport: { width: 1920, height: 1080 } }],
];

function inspect(target) {
  const nodes = [...document.querySelectorAll("[data-component]")];
  const width = document.documentElement.clientWidth;
  const notes = [];
  if (document.documentElement.scrollWidth > width + 1) {
    notes.push(`страница прокручивается вбок: ${document.documentElement.scrollWidth} при ширине ${width}`);
  }
  for (const el of nodes) {
    const r = el.getBoundingClientRect();
    if (el.scrollWidth > el.clientWidth + 1) {
      notes.push(`${el.dataset.component}: содержимое шире себя (${el.scrollWidth} в ${el.clientWidth})`);
    }
    if (r.left < -1 || r.right > width + 1) {
      notes.push(`${el.dataset.component}: за краем окна ${Math.round(r.left)}…${Math.round(r.right)}`);
    }
  }
  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      const [a, b] = [nodes[i], nodes[j]];
      if (a.contains(b) || b.contains(a)) continue;
      const [ra, rb] = [a.getBoundingClientRect(), b.getBoundingClientRect()];
      const area = Math.max(0, Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left))
        * Math.max(0, Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top));
      if (area > 1) notes.push(`наложение ${a.dataset.component} ↔ ${b.dataset.component}: ${Math.round(area)}px²`);
    }
  }
  for (const el of document.querySelectorAll("button, a[href], input, select, textarea, [role=button]")) {
    const r = el.getBoundingClientRect();
    if (r.width && (r.width < target || r.height < target)) {
      const label = (el.getAttribute("aria-label") || el.textContent || el.tagName).trim().slice(0, 30);
      notes.push(`цель касания ${Math.round(r.width)}×${Math.round(r.height)} мельче ${target}: «${label}»`);
    }
  }
  return [...new Set(notes)];
}

mkdirSync(out, { recursive: true });
const server = spawn("npx", ["vite", "preview", "--port", String(PORT), "--strictPort"],
  { cwd: path.join(ROOT, tree), stdio: "ignore" });
const executablePath = process.env.PW_CHROMIUM || (existsSync(PRESET_CHROMIUM) ? PRESET_CHROMIUM : undefined);
let total = 0;
try {
  const browser = await chromium.launch({ executablePath });
  for (let attempt = 0; ; attempt++) {
    try {
      await fetch(`http://localhost:${PORT}/`);
      break;
    } catch {
      if (attempt > 50) throw new Error("vite preview не поднялся");
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
  }
  for (const [name, profile] of PROFILES) {
    const { defaultBrowserType: _engine, ...options } = profile;
    const context = await browser.newContext({ ...options, colorScheme: "dark", locale: "ru-RU" });
    const page = await context.newPage();
    await page.goto(`http://localhost:${PORT}/`);
    await page.waitForSelector("[data-component]");
    const notes = await page.evaluate(inspect, TARGET);
    await page.screenshot({ path: path.join(out, `${name}.png`), fullPage: true });
    console.log(`── ${name} ${options.viewport.width}×${options.viewport.height}: ${notes.length ? notes.length + " шт." : "чисто"}`);
    for (const note of notes) console.log(`   ✗ ${note}`);
    total += notes.length;
    await context.close();
  }
  await browser.close();
} finally {
  server.kill();
}
console.log(`снимки: ${out}`);
process.exit(total ? 1 : 0);
