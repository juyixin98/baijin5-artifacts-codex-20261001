import { afterEach, describe, expect, it } from 'vitest';
import { rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { ResourceRepository } from '../src/state/repository.js';
import { FIXTURES } from '../src/state/fixtures.js';

const tempPaths: string[] = [];
afterEach(() => {
  for (const path of tempPaths) rmSync(path, { recursive: true, force: true });
});

describe('ResourceRepository — SQLite state adapter with synthetic fixtures', () => {
  it('seeds fixtures and maps rows into normalized kernel candidates', () => {
    const repo = ResourceRepository.open(':memory:');
    expect(repo.isSeeded()).toBe(false);
    repo.seed();
    expect(repo.isSeeded()).toBe(true);

    const welcome = repo.getByPath('/welcome');
    expect(welcome.id).toBe('welcome');
    expect(welcome.representations).toHaveLength(5);

    const plain = welcome.representations.find((rep) => rep.id === 'welcome-plain-en')!;
    expect(plain).toMatchObject({
      type: 'text',
      subtype: 'plain',
      language: 'en',
      ordinal: 4,
      params: { charset: 'utf-8' },
    });
    expect(plain.body).toContain('Welcome');

    const htmlZh = welcome.representations.find((rep) => rep.id === 'welcome-html-zh')!;
    expect(htmlZh.language).toBe('zh-cn');
    expect(htmlZh.ordinal).toBe(1);
    repo.close();
  });

  it('seeds is idempotent-safe per fresh database and persists to disk', () => {
    const dir = join(tmpdir(), `opp408-db-${process.pid}-${Date.now()}`);
    tempPaths.push(dir);
    const dbPath = join(dir, 'nested', 'service.db');

    const first = ResourceRepository.open(dbPath);
    first.seed();
    first.close();

    const second = ResourceRepository.open(dbPath);
    expect(second.isSeeded()).toBe(true);
    expect(second.listPaths()).toEqual(['/report', '/welcome']);
    expect(() => second.getByPath('/nope')).toThrow(ReferenceError);
    second.close();
  });

  it('fixture data itself covers multiple media types, params and languages', () => {
    const repo = ResourceRepository.open(':memory:');
    repo.seed(FIXTURES);
    const report = repo.getByPath('/report');
    expect(report.representations.map((r) => `${r.type}/${r.subtype}:${r.language}`)).toEqual([
      'application/json:en',
      'application/json:fr',
      'application/csv:en',
    ]);
    repo.close();
  });
});
