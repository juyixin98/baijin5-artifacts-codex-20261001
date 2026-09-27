/**
 * Resolve the running service version from package.json. The relative path
 * has the same depth from src/core (type-stripped execution) and dist/core
 * (compiled execution), so one lookup works in both modes.
 */
import { readFileSync } from 'node:fs';

let cached: string | null = null;

export function serviceVersion(): string {
  if (cached !== null) return cached;
  try {
    const pkg = JSON.parse(readFileSync(new URL('../../package.json', import.meta.url), 'utf8')) as { version?: unknown };
    cached = typeof pkg.version === 'string' ? pkg.version : '0.0.0-unknown';
  } catch {
    cached = '0.0.0-unknown';
  }
  return cached;
}
