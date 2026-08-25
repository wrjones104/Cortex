import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    // fake-indexeddb gives the queue a real IndexedDB implementation, so the
    // tests exercise the same code paths a browser does rather than a mock.
    setupFiles: ["./src/test/setup.ts"],
  },
});
