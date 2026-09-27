/**
 * Configuration loader — independent module (config/negotiation.config.json),
 * validated at startup. Invalid configuration fails fast instead of being
 * silently replaced by defaults.
 */

import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';

export interface ServiceConfig {
  configVersion: string;
  port: number;
  databasePath: string;
  seedFixtures: boolean;
  maxAcceptEntries: number;
  languageFallback: { enabled: boolean; penaltyPerStrippedSubtag: number };
  invalidEntryPolicy: 'drop-with-warning' | 'reject-header' | 'ignore';
  duplicatePolicy: 'first-wins' | 'last-wins';
  tieBreak: string[];
  traceBufferSize: number;
}

export async function loadConfig(path: string): Promise<ServiceConfig> {
  const raw = await readFile(path, 'utf8');
  const parsed: unknown = JSON.parse(raw);
  return validateConfig(parsed, path);
}

function fail(path: string, message: string): never {
  throw new Error(`Invalid configuration ${path}: ${message}`);
}

function validateConfig(value: unknown, path: string): ServiceConfig {
  if (typeof value !== 'object' || value === null) fail(path, 'root must be an object');
  const cfg = value as Record<string, unknown>;
  const requireString = (key: string): string => {
    if (typeof cfg[key] !== 'string' || (cfg[key] as string) === '') fail(path, `${key} must be a non-empty string`);
    return cfg[key] as string;
  };
  const requireNumber = (key: string, min: number, max: number): number => {
    const v = cfg[key];
    if (typeof v !== 'number' || !Number.isFinite(v) || v < min || v > max) {
      fail(path, `${key} must be a number in [${min}, ${max}]`);
    }
    return v;
  };

  const policy = requireString('invalidEntryPolicy');
  if (!['drop-with-warning', 'reject-header', 'ignore'].includes(policy)) fail(path, 'bad invalidEntryPolicy');
  const dup = requireString('duplicatePolicy');
  if (!['first-wins', 'last-wins'].includes(dup)) fail(path, 'bad duplicatePolicy');

  const fb = cfg.languageFallback;
  if (typeof fb !== 'object' || fb === null) fail(path, 'languageFallback required');
  const fallback = fb as Record<string, unknown>;
  if (typeof fallback.enabled !== 'boolean') fail(path, 'languageFallback.enabled must be boolean');
  if (typeof fallback.penaltyPerStrippedSubtag !== 'number' || !(fallback.penaltyPerStrippedSubtag > 0 && fallback.penaltyPerStrippedSubtag <= 1)) {
    fail(path, 'languageFallback.penaltyPerStrippedSubtag must be in (0,1]');
  }

  const tieBreak = cfg.tieBreak;
  if (!Array.isArray(tieBreak) || tieBreak.some((x) => typeof x !== 'string')) fail(path, 'tieBreak must be string[]');

  return {
    configVersion: requireString('configVersion'),
    port: requireNumber('port', 1, 65535),
    databasePath: requireString('databasePath'),
    seedFixtures: cfg.seedFixtures === true,
    maxAcceptEntries: requireNumber('maxAcceptEntries', 1, 10000),
    languageFallback: {
      enabled: fallback.enabled,
      penaltyPerStrippedSubtag: fallback.penaltyPerStrippedSubtag,
    },
    invalidEntryPolicy: policy as ServiceConfig['invalidEntryPolicy'],
    duplicatePolicy: dup as ServiceConfig['duplicatePolicy'],
    tieBreak: tieBreak as string[],
    traceBufferSize: requireNumber('traceBufferSize', 1, 100000),
  };
}

export function configAbsolutePath(configPath: string): string {
  return resolve(configPath);
}
