import { describe, expect, it } from 'vitest';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { loadConfig } from '../src/state/configLoader.js';

const VALID = {
  configVersion: '1.0.0',
  port: 4080,
  databasePath: 'data/negotiation.db',
  seedFixtures: true,
  maxAcceptEntries: 50,
  languageFallback: { enabled: true, penaltyPerStrippedSubtag: 0.9 },
  invalidEntryPolicy: 'drop-with-warning',
  duplicatePolicy: 'first-wins',
  tieBreak: ['mediaQ desc', 'languageQ desc', 'ordinal asc'],
  traceBufferSize: 128,
};

async function writeConfig(value: unknown): Promise<string> {
  const dir = await mkdtemp(join(tmpdir(), 'opp408-cfg-'));
  const path = join(dir, 'config.json');
  await writeFile(path, JSON.stringify(value), 'utf8');
  return path;
}

describe('loadConfig — independent, fail-fast configuration validation', () => {
  it('loads a valid file verbatim with normalized types', async () => {
    const path = await writeConfig(VALID);
    const config = await loadConfig(path);
    expect(config.port).toBe(4080);
    expect(config.languageFallback.penaltyPerStrippedSubtag).toBe(0.9);
    expect(config.tieBreak).toHaveLength(3);
    await rm(join(path, '..'), { recursive: true });
  });

  it.each([
    ['non-object root', null, 'root must be an object'],
    ['bad policy', { ...VALID, invalidEntryPolicy: 'nope' }, 'bad invalidEntryPolicy'],
    ['bad duplicate policy', { ...VALID, duplicatePolicy: 'maybe' }, 'bad duplicatePolicy'],
    ['port out of range', { ...VALID, port: 70000 }, 'port'],
    ['non-boolean fallback flag', { ...VALID, languageFallback: { enabled: 1, penaltyPerStrippedSubtag: 0.9 } }, 'enabled'],
    ['penalty zero', { ...VALID, languageFallback: { enabled: true, penaltyPerStrippedSubtag: 0 } }, 'penalty'],
    ['tieBreak not strings', { ...VALID, tieBreak: [1, 2] }, 'tieBreak'],
    ['missing version', { ...VALID, configVersion: '' }, 'configVersion'],
  ])('rejects %s with a clear error', async (_label, value, fragment) => {
    const path = await writeConfig(value);
    await expect(loadConfig(path)).rejects.toThrow(fragment as string);
    await rm(join(path, '..'), { recursive: true, force: true });
  });

  it('propagates malformed JSON as a startup failure rather than defaulting silently', async () => {
    const dir = await mkdtemp(join(tmpdir(), 'opp408-cfg-'));
    const path = join(dir, 'broken.json');
    await writeFile(path, '{ not json', 'utf8');
    await expect(loadConfig(path)).rejects.toBeInstanceOf(SyntaxError);
    await rm(dir, { recursive: true, force: true });
  });
});
