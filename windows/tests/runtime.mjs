// Loads production TypeScript and raw localization imports through Vite, without
// starting the development collector server or mounting the application.
import { createServer } from "vite";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

export async function testRuntime() {
  const values = new Map([["appLanguage", JSON.stringify("zh")]]);
  const names = ["window", "navigator", "localStorage"];
  const descriptors = names.map(name => Object.getOwnPropertyDescriptor(globalThis, name));
  const globals = {
    window: { addEventListener() {}, removeEventListener() {} },
    navigator: { language: "zh-CN", languages: ["zh-CN"] },
    localStorage: {
      getItem: key => values.get(key) ?? null,
      setItem: (key, value) => values.set(key, value),
      removeItem: key => values.delete(key),
    },
  };
  for (const name of names) Object.defineProperty(globalThis, name, { configurable: true, value: globals[name] });
  const cache = await mkdtemp(path.join(tmpdir(), "tokei-web-tests-"));
  let server;
  const restore = () => names.forEach((name, i) => {
    if (descriptors[i]) Object.defineProperty(globalThis, name, descriptors[i]);
    else delete globalThis[name];
  });
  try {
    server = await createServer({
      configFile: false,
      root: fileURLToPath(new URL("../", import.meta.url)),
      cacheDir: cache,
      logLevel: "error",
      // SSR assertions need no client optimizer; avoid background output after close.
      optimizeDeps: { noDiscovery: true, include: [] },
      server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    });
    return {
      load: file => server.ssrLoadModule("/" + file),
      pref: (key, value) => values.set(key, JSON.stringify(value)),
      clear: () => { values.clear(); values.set("appLanguage", JSON.stringify("zh")); },
      async close() { await server.close(); restore(); await rm(cache, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 }); },
    };
  } catch (error) {
    if (server) await server.close();
    restore(); await rm(cache, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 });
    throw error;
  }
}
