// Downloads the UI fonts from Fontshare into public/fonts so the app works offline.
// The files are not committed: the Fontshare licence allows use, not redistribution.
// If the download fails the UI falls back to system fonts, so this never breaks a build.
import { existsSync } from "node:fs";
import { mkdir, writeFile } from "node:fs/promises";

const OUT = new URL("../public/fonts/", import.meta.url);
const FAMILIES = ["switzer@400,500,600", "cabinet-grotesk@700,800", "jet-brains-mono@400,500"];
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36";

if (existsSync(new URL("fonts.css", OUT))) process.exit(0);

try {
  await mkdir(OUT, { recursive: true });
  let css = "";
  for (const family of FAMILIES) {
    const res = await fetch(`https://api.fontshare.com/v2/css?f[]=${family}&display=swap`, { headers: { "User-Agent": UA } });
    if (!res.ok) throw new Error(`${res.status} for ${family}`);
    css += await res.text();
  }
  const urls = [...new Set([...css.matchAll(/url\(['"]?([^'")]+)['"]?\)/g)].map((m) => m[1]))].filter((u) => u.includes(".woff2"));
  if (!urls.length) throw new Error("no woff2 files in the Fontshare stylesheet");
  let n = 0;
  for (const u of urls) {
    const res = await fetch(u.startsWith("//") ? `https:${u}` : u, { headers: { "User-Agent": UA } });
    if (!res.ok) throw new Error(`${res.status} for ${u}`);
    const name = `font-${n++}.woff2`;
    await writeFile(new URL(name, OUT), Buffer.from(await res.arrayBuffer()));
    css = css.replaceAll(u, `/fonts/${name}`);
  }
  // keep only the woff2 sources
  css = css.replace(/,?\s*url\([^)]*\.(woff|ttf)['"]?\)\s*format\(['"](woff|truetype)['"]\)/g, "");
  await writeFile(new URL("fonts.css", OUT), css);
  console.log(`fonts: ${n} files from Fontshare`);
} catch (e) {
  console.warn(`fonts: skipped (${e.message}); the UI will use system fonts`);
}
