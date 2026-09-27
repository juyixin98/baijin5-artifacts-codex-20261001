/**
 * Test-only bridge to Node's built-in node:sqlite.
 *
 * The Vite version bundled with Vitest predates the builtin allowlist
 * entry for `node:sqlite`, so it tries (and fails) to bundle it. Alias
 * `node:sqlite` -> this file in vitest.config; the builtin name is built
 * at runtime so the static analyzer does not rewrite it. Production code
 * imports `node:sqlite` directly — this shim is never used outside tests.
 */

import { createRequire } from 'node:module';

const localRequire = createRequire(import.meta.url);
const builtinName = ['node:', 'sqlite'].join('');
const sqlite = localRequire(builtinName) as typeof import('node:sqlite');

export const DatabaseSync = sqlite.DatabaseSync;
