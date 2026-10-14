import type { Circuit, GraphicsSettings } from "../core/types";
import type { BenchmarkReport } from "../graphics/benchmark";
const dbPromise = new Promise<IDBDatabase>((resolve, reject) => {
  const request = indexedDB.open("apex-racing-lab", 1);
  request.onupgradeneeded = () => {
    for (const name of ["circuits", "benchmarks", "settings"])
      request.result.createObjectStore(name);
  };
  request.onsuccess = () => resolve(request.result);
  request.onerror = () =>
    reject(new Error("Não foi possível abrir o armazenamento local."));
});
async function read<T>(
  store: string,
  key: IDBValidKey,
): Promise<T | undefined> {
  const db = await dbPromise;
  return new Promise((resolve, reject) => {
    const req = db.transaction(store).objectStore(store).get(key);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}
async function write(store: string, key: IDBValidKey, value: unknown) {
  const db = await dbPromise;
  return new Promise<void>((resolve, reject) => {
    const tx = db.transaction(store, "readwrite");
    tx.objectStore(store).put(value, key);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error);
  });
}
async function all<T>(store: string): Promise<T[]> {
  const db = await dbPromise;
  return new Promise((resolve, reject) => {
    const req = db.transaction(store).objectStore(store).getAll();
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}
export const storage = {
  circuits: () => all<Circuit>("circuits"),
  saveCircuit: (c: Circuit) => write("circuits", `${c.id}@${c.version}`, c),
  benchmarks: () => all<BenchmarkReport>("benchmarks"),
  saveBenchmark: (r: BenchmarkReport) => write("benchmarks", r.id, r),
  settings: () => read<GraphicsSettings>("settings", "graphics"),
  saveSettings: (s: GraphicsSettings) => write("settings", "graphics", s),
  readPreference: <T>(key: string) => read<T>("settings", key),
  savePreference: (key: string, value: unknown) =>
    write("settings", key, value),
};
