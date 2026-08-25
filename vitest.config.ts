import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    include: ['packages/*/test/**/*.test.ts', 'tests/vitest/**/*.test.ts'],
    exclude: ['**/node_modules/**', '**/dist/**'],
    // Container-backed integration tests need room; the in-process suites are fast.
    testTimeout: 180_000,
    hookTimeout: 180_000,
    reporters: ['verbose'],
  },
});
