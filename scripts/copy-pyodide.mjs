// Copies the Pyodide runtime (CPython compiled to WebAssembly) from the npm
// package into public/pyodide/ so Vite serves it from the same origin. The game
// therefore needs no CDN or remote physics server at run time.
import { copyFileSync, mkdirSync, existsSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const source = join(root, "node_modules", "pyodide");
const target = join(root, "public", "pyodide");
const files = [
  "pyodide.mjs",
  "pyodide.asm.mjs",
  "pyodide.asm.wasm",
  "python_stdlib.zip",
  "pyodide-lock.json",
  "package.json",
];
if (!existsSync(source)) {
  console.error("pyodide não encontrado em node_modules. Execute npm ci.");
  process.exit(1);
}
mkdirSync(target, { recursive: true });
let bytes = 0;
for (const file of files) {
  copyFileSync(join(source, file), join(target, file));
  bytes += statSync(join(target, file)).size;
}
console.log(`Pyodide copiado para public/pyodide (${(bytes / 1048576).toFixed(1)} MB).`);
