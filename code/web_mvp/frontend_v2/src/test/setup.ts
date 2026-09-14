import "@testing-library/jest-dom/vitest";

// Node 26 disables jsdom's implicit localStorage unless a storage file is
// supplied. Keep browser-facing tests deterministic without requiring a
// machine-specific NODE_OPTIONS flag.
try {
  if (!window.localStorage) throw new Error("localStorage unavailable");
} catch {
  const values = new Map<string, string>();
  const storage: Storage = {
    get length() {
      return values.size;
    },
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    key: (index) => [...values.keys()][index] ?? null,
    removeItem: (key) => values.delete(key),
    setItem: (key, value) => values.set(key, String(value)),
  };
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: storage,
  });
}

Object.defineProperty(window.HTMLElement.prototype, "scrollIntoView", {
  configurable: true,
  value: () => undefined,
});

Object.defineProperty(window, "requestAnimationFrame", {
  configurable: true,
  writable: true,
  value: (callback: FrameRequestCallback) => window.setTimeout(() => callback(performance.now()), 0),
});
