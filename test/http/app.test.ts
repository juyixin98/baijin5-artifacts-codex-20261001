import { describe, it, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { FastifyInstance } from 'fastify';
import { buildApp } from '../../src/http/app.js';
import { loadConfig } from '../../src/config.js';
import { ResourceRepository, applySchema, seedDatabase } from '../../src/state/repository.js';

const root = resolve(fileURLToPath(new URL('.', import.meta.url)), '..', '..', '..');

/** Silence HTTP logging in tests while keeping every negotiation policy default. */
function testConfig() {
  const config = loadConfig();
  return { ...config, logging: { ...config.logging, level: 'silent' } };
}

describe('HTTP layer - negotiated resource endpoint', () => {
  let app: FastifyInstance;
  let db: DatabaseSync;

  before(async () => {
    db = new DatabaseSync(':memory:');
    applySchema(db, readFileSync(resolve(root, 'src', 'state', 'schema.sql'), 'utf8'));
    seedDatabase(db, resolve(root, 'data', 'seed.json'));
    const config = testConfig();
    app = await buildApp({ config, repository: new ResourceRepository(db) });
    await app.ready();
  });

  after(async () => {
    await app.close();
    db.close();
  });

  it('200: exact json + en returns the r0 body with matching content headers', async () => {
    const res = await app.inject({ method: 'GET', url: '/resources/article-001', headers: { Accept: 'application/json', 'Accept-Language': 'en' } });
    assert.equal(res.statusCode, 200);
    assert.match(res.headers['content-type']!, /application\/json/);
    assert.match(res.headers['content-type']!, /version=1/);
    assert.equal(res.headers['content-language'], 'en');
    assert.equal(res.headers['x-selected-representation'], 'article-001#r0');
    assert.ok((res.headers['vary'] ?? '').includes('Accept'));
    assert.ok((res.headers['vary'] ?? '').includes('Accept-Language'));
    assert.match(String(res.headers['x-negotiation-run-id']), /^[0-9a-f-]{36}$/);
    const body = JSON.parse(res.body) as { schema: string };
    assert.equal(body.schema, 'v1');
  });

  it('200: vendor v2 + fr selects the French v2 representation', async () => {
    const res = await app.inject({ method: 'GET', url: '/resources/article-001', headers: { Accept: 'application/vnd.shop.v2+json', 'Accept-Language': 'fr' } });
    assert.equal(res.statusCode, 200);
    assert.match(res.headers['content-type']!, /vnd\.shop\.v2\+json/);
    assert.equal(res.headers['content-language'], 'fr');
    assert.equal(res.headers['x-selected-representation'], 'article-001#r3');
  });

  it('200: language truncation fallback en-us serves en', async () => {
    const res = await app.inject({ method: 'GET', url: '/resources/article-001', headers: { Accept: 'application/json', 'Accept-Language': 'en-us' } });
    assert.equal(res.statusCode, 200);
    assert.equal(res.headers['content-language'], 'en');
  });

  it('406: unsupported media type is a classified failure, not a 200', async () => {
    const res = await app.inject({ method: 'GET', url: '/resources/article-001', headers: { Accept: 'image/avif', 'Accept-Language': 'en' } });
    assert.equal(res.statusCode, 406);
    const payload = JSON.parse(res.body) as { error: string; stage: string };
    assert.equal(payload.error, 'UNACCEPTABLE_MEDIA_TYPE');
    assert.equal(payload.stage, 'negotiate-media');
    assert.ok(Array.isArray((payload as { offered?: unknown[] }).offered));
  });

  it('406: explicit q=0 prohibition of every media type', async () => {
    const res = await app.inject({ method: 'GET', url: '/resources/article-001', headers: { Accept: '*/*;q=0', 'Accept-Language': 'en' } });
    assert.equal(res.statusCode, 406);
    assert.equal((JSON.parse(res.body) as { error: string }).error, 'UNACCEPTABLE_MEDIA_TYPE');
  });

  it('406: unsupported language is reported on the language axis', async () => {
    const res = await app.inject({ method: 'GET', url: '/resources/article-001', headers: { Accept: 'application/json', 'Accept-Language': 'ko' } });
    assert.equal(res.statusCode, 406);
    const payload = JSON.parse(res.body) as { error: string; stage: string };
    assert.equal(payload.error, 'UNACCEPTABLE_LANGUAGE');
    assert.equal(payload.stage, 'negotiate-language');
  });

  it('400: an illegal weight is rejected rather than negotiated', async () => {
    const res = await app.inject({ method: 'GET', url: '/resources/article-001', headers: { Accept: 'text/html;q=9', 'Accept-Language': 'en' } });
    assert.equal(res.statusCode, 400);
    const payload = JSON.parse(res.body) as { error: string };
    assert.equal(payload.error, 'INVALID_WEIGHT');
  });

  it('400: a malformed language tag is rejected', async () => {
    const res = await app.inject({ method: 'GET', url: '/resources/article-001', headers: { Accept: '*/*', 'Accept-Language': 'en-' } });
    assert.equal(res.statusCode, 400);
    assert.equal((JSON.parse(res.body) as { error: string }).error, 'MALFORMED_HEADER');
  });

  it('404: unknown resource is distinct from a negotiation failure', async () => {
    const res = await app.inject({ method: 'GET', url: '/resources/nope', headers: { Accept: '*/*' } });
    assert.equal(res.statusCode, 404);
    assert.equal((JSON.parse(res.body) as { error: string }).error, 'NOT_FOUND');
  });

  it('200: absent headers negotiate a default variant and set Vary on the varying axes', async () => {
    const res = await app.inject({ method: 'GET', url: '/resources/greeting' });
    assert.equal(res.statusCode, 200);
    const vary = (res.headers['vary'] ?? '') as string;
    assert.ok(vary.includes('Accept-Language'));
  });
});

describe('HTTP layer - diagnostic trace endpoint', () => {
  let app: FastifyInstance;
  let db: DatabaseSync;

  before(async () => {
    db = new DatabaseSync(':memory:');
    applySchema(db, readFileSync(resolve(root, 'src', 'state', 'schema.sql'), 'utf8'));
    seedDatabase(db, resolve(root, 'data', 'seed.json'));
    app = await buildApp({ config: testConfig(), repository: new ResourceRepository(db) });
    await app.ready();
  });

  after(async () => {
    await app.close();
    db.close();
  });

  it('200: explains a successful decision with scores, steps and run identity', async () => {
    // vnd.shop.v2+json exists in both fr and en, so the higher fr weight wins.
    const res = await app.inject({ method: 'GET', url: '/resources/article-001/trace', headers: { Accept: 'application/vnd.shop.v2+json', 'Accept-Language': 'fr;q=0.9, en;q=0.8' } });
    assert.equal(res.statusCode, 200);
    const payload = JSON.parse(res.body) as {
      trace: {
        runId: string;
        serviceVersion: string;
        winner: { representationId: string; language: string };
        candidateScores: unknown[];
        steps: string[];
        vary: string[];
      };
    };
    assert.match(payload.trace.runId, /^[0-9a-f-]{36}$/);
    assert.equal(payload.trace.serviceVersion, '1.0.0');
    assert.equal(payload.trace.winner.language, 'fr');
    assert.equal(payload.trace.winner.representationId, 'article-001#r3');
    assert.ok(payload.trace.candidateScores.length >= 11);
    assert.ok(payload.trace.steps.at(-1)!.startsWith('verdict: WINNER'));
    assert.deepEqual(payload.trace.vary, ['Accept', 'Accept-Language']);
  });

  it('200: a media pin can override language preference (json has no fr -> en wins)', async () => {
    const res = await app.inject({ method: 'GET', url: '/resources/article-001/trace', headers: { Accept: 'application/json', 'Accept-Language': 'fr;q=0.9, en;q=0.8' } });
    assert.equal(res.statusCode, 200);
    const payload = JSON.parse(res.body) as { trace: { winner: { representationId: string; language: string } } };
    assert.equal(payload.trace.winner.language, 'en');
    assert.equal(payload.trace.winner.representationId, 'article-001#r0');
  });

  it('mirrors the real 406 status for a failed negotiation (never reports success)', async () => {
    const res = await app.inject({ method: 'GET', url: '/resources/article-001/trace', headers: { Accept: 'image/avif' } });
    assert.equal(res.statusCode, 406);
    const payload = JSON.parse(res.body) as { trace: { failure: { code: string } } };
    assert.equal(payload.trace.failure.code, 'UNACCEPTABLE_MEDIA_TYPE');
  });

  it('404s on an unknown resource', async () => {
    const res = await app.inject({ method: 'GET', url: '/resources/missing/trace' });
    assert.equal(res.statusCode, 404);
  });
});

describe('HTTP layer - resource catalogue', () => {
  it('lists resources with their offered variants', async () => {
    const db = new DatabaseSync(':memory:');
    applySchema(db, readFileSync(resolve(root, 'src', 'state', 'schema.sql'), 'utf8'));
    seedDatabase(db, resolve(root, 'data', 'seed.json'));
    const app = await buildApp({ config: testConfig(), repository: new ResourceRepository(db) });
    const res = await app.inject({ method: 'GET', url: '/resources' });
    assert.equal(res.statusCode, 200);
    const payload = JSON.parse(res.body) as { resources: Array<{ id: string; variants: unknown[] }> };
    assert.equal(payload.resources.length, 2);
    assert.ok(payload.resources.find((r) => r.id === 'greeting')!.variants.length === 5);
    await app.close();
    db.close();
  });
});
