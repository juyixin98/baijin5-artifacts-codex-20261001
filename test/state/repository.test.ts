import { describe, it, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  ResourceRepository,
  applySchema,
  isSeeded,
  seedDatabase,
} from '../../src/state/repository.js';
import { negotiate } from '../../src/core/negotiator.js';

const root = resolve(fileURLToPath(new URL('.', import.meta.url)), '..', '..', '..');
const schemaSql = readFileSync(resolve(root, 'src', 'state', 'schema.sql'), 'utf8');
const seedPath = resolve(root, 'data', 'seed.json');

describe('SQLite state adapter', () => {
  let db: DatabaseSync;
  let repo: ResourceRepository;

  before(() => {
    db = new DatabaseSync(':memory:');
    applySchema(db, schemaSql);
  });

  after(() => db.close());

  it('starts unseeded', () => {
    assert.equal(isSeeded(db), false);
  });

  it('seeds resources and representations from the synthetic fixture', () => {
    const result = seedDatabase(db, seedPath);
    assert.ok(result.resources >= 2);
    assert.ok(result.representations >= 10);
    assert.equal(isSeeded(db), true);
  });

  it('is idempotent: reseeding replaces rather than duplicating rows', () => {
    const first = seedDatabase(db, seedPath);
    const countRow = db.prepare('SELECT COUNT(*) AS n FROM representations').get() as { n: number };
    seedDatabase(db, seedPath);
    const countRow2 = db.prepare('SELECT COUNT(*) AS n FROM representations').get() as { n: number };
    assert.equal(countRow2.n, countRow.n);
    assert.equal(countRow2.n, first.representations);
  });

  it('lists known resource ids', () => {
    repo = new ResourceRepository(db);
    const ids = repo.listResourceIds();
    assert.deepEqual(ids, ['article-001', 'greeting']);
  });

  it('returns null for an unknown resource', () => {
    assert.equal(repo.getResource('does-not-exist'), null);
  });

  it('maps a resource and orders representations by ordinal', () => {
    const resource = repo.getResource('article-001')!;
    assert.equal(resource.representations.length, 11);
    assert.equal(resource.representations[0]!.id, 'article-001#r0');
    assert.equal(resource.representations[0]!.mediaType.subtype, 'json');
  });

  it('round-trips media parameters as a lower-cased constraint map', () => {
    const resource = repo.getResource('article-001')!;
    const v0 = resource.representations[0]!;
    assert.equal(v0.mediaType.parameters.get('version'), '1');
  });

  it('integrates with the negotiation core: versioned constraint selects r0', () => {
    const resource = repo.getResource('article-001')!;
    const trace = negotiate({
      resource,
      acceptHeader: 'application/json; version=1',
      acceptLanguageHeader: 'en',
    });
    assert.equal(trace.failure, null);
    assert.equal(trace.winner!.representationId, 'article-001#r0');
  });

  it('integrates with the negotiation core: vendor v2 + fr selects r3', () => {
    const resource = repo.getResource('article-001')!;
    const trace = negotiate({
      resource,
      acceptHeader: 'application/vnd.shop.v2+json',
      acceptLanguageHeader: 'fr',
    });
    assert.equal(trace.failure, null);
    assert.equal(trace.winner!.representationId, 'article-001#r3');
    assert.equal(trace.winner!.language, 'fr');
  });
});

describe('SQLite state adapter - schema constraints', () => {
  it('rejects an ordinal below zero via CHECK', () => {
    const db2 = new DatabaseSync(':memory:');
    applySchema(db2, schemaSql);
    db2.prepare('INSERT INTO resources (id, name) VALUES (?, ?)').run('x', 'x');
    assert.throws(
      () =>
        db2
          .prepare('INSERT INTO representations (id, resource_id, ordinal, media_type, media_subtype, media_params, language, charset, body) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)')
          .run('x#r0', 'x', -1, 'text', 'plain', '{}', 'en', 'utf-8', 'b'),
      /CHECK|constraint/i,
    );
    db2.close();
  });

  it('cascades representation deletion when a resource is removed', () => {
    const db2 = new DatabaseSync(':memory:');
    applySchema(db2, schemaSql);
    seedDatabase(db2, seedPath);
    db2.prepare('DELETE FROM resources WHERE id = ?').run('greeting');
    const orphan = db2.prepare("SELECT COUNT(*) AS n FROM representations WHERE resource_id = 'greeting'").get() as { n: number };
    assert.equal(orphan.n, 0);
    db2.close();
  });
});
