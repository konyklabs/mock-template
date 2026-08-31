import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    globalSetup: ["./setup/global.ts"],
    include: ["tests/**/*.test.ts"],
    // A container pull on a cold machine can take a while; the tests
    // themselves finish in well under a second each.
    testTimeout: 60_000,
    hookTimeout: 180_000,
  },
});
