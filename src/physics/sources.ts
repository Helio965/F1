/**
 * The Python packages are bundled as text at build time (Vite glob import) and
 * written into Pyodide's in-memory filesystem: the same files that pytest runs
 * on CPython are the ones executed in the browser - one implementation only.
 */
const modules = import.meta.glob(
  "../../python/{physics_engine,track_analyzer,racing_ai,apex_bridge}/*.py",
  { query: "?raw", import: "default", eager: true },
) as Record<string, string>;

export interface PythonSource {
  path: string;
  text: string;
}

export const PYTHON_ROOT = "/home/pyodide/apex";

export const PYTHON_SOURCES: PythonSource[] = Object.entries(modules)
  .map(([key, text]) => ({ path: key.replace(/^.*\/python\//, ""), text }))
  .sort((a, b) => a.path.localeCompare(b.path));

interface PyodideFS {
  mkdirTree(path: string): void;
  writeFile(path: string, data: string): void;
}

/** Write the sources into the virtual FS and put them on sys.path. */
export function installSources(
  pyodide: { FS: PyodideFS; runPython(code: string): unknown },
  sources: PythonSource[] = PYTHON_SOURCES,
): void {
  if (!sources.length) throw new Error("Fontes Python do motor físico ausentes.");
  for (const { path, text } of sources) {
    const full = `${PYTHON_ROOT}/${path}`;
    pyodide.FS.mkdirTree(full.slice(0, full.lastIndexOf("/")));
    pyodide.FS.writeFile(full, text);
  }
  pyodide.runPython(
    `import sys\nif ${JSON.stringify(PYTHON_ROOT)} not in sys.path:\n    sys.path.insert(0, ${JSON.stringify(PYTHON_ROOT)})`,
  );
}
