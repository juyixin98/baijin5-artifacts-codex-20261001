import { defineConfig } from 'vitest/config';
import { fileURLToPath } from 'node:url';

export default defineConfig({
  resolve: {
    alias: [
      {
        find: /^node:sqlite$/,
        replacement: fileURLToPath(new URL('./test/support/sqlite-shim.ts', import.meta.url)),
      },
    ],
  },
  test: {
    include: ['test/**/*.test.ts'],
    coverage: {
      provider: 'v8',
      include: ['src/**/*.ts'],
      // type-only modules emit no runtime code; the process entry is
      // covered by way of the real HTTP smoke test run separately.
      exclude: ['src/server.ts', 'src/version.ts', 'src/contract/types.ts'],
      thresholds: { statements: 80, branches: 80, functions: 80, lines: 80 },
    },
  },
});
