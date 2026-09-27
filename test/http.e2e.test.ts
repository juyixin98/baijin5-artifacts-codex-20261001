/**
 * End-to-end tests over a REAL Fastify instance and a REAL in-memory
 * SQLite database (no stubbed interfaces). Assertions cover concrete
 * selected representations, statuses, failure categories, Vary and the
 * structured run logs' correlation id — not merely "endpoint callable".
 */

import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { FastifyInstance } from 'fastify';
import { buildApp, type BuiltApp } from '../src/app.js';
import type { ServiceConfig } from '../src/state/configLoader.js';
import type { ResourceRepository } from '../src/state/repository.js';
import type { TraceStore } from '../src/state/traceStore.js';

function testConfig(overrides: Partial<ServiceConfig> = {}): ServiceConfig {
  return {
    configVersion: '1.0.0-test',
    port: 0,
    databasePath: ':memory:',
    seedFixtures: true,
    maxAcceptEntries: 50,
    languageFallback: { enabled: true, penaltyPerStrippedSubtag: 0.9 },
    invalidEntryPolicy: 'drop-with-warning',
    duplicatePolicy: 'first-wins',
    tieBreak: ['mediaQ desc', 'languageQ desc', 'ordinal asc'],
    traceBufferSize: 16,
    ...overrides,
  };
}

let built: BuiltApp | null = null;
let logs: string[] = [];

async function startApp(overrides: Partial<ServiceConfig> = {}): Promise<{
  app: FastifyInstance;
  repo: ResourceRepository;
  traces: TraceStore;
}> {
  logs = [];
  built = await buildApp(testConfig(overrides), (line) => {
    logs.push(line);
  });
  return built;
}

beforeEach(() => {
  logs = [];
});

afterEach(async () => {
  if (built) {
    await built.app.close();
    built.repo.close();
    built = null;
  }
});

describe('GET resource — successful selection', () => {
  it('returns the exact JSON zh-cn representation with matching response headers', async () => {
    const { app } = await startApp();
    const res = await app.inject({
      method: 'GET',
      url: '/welcome',
      headers: { Accept: 'application/json', 'Accept-Language': 'zh-CN' },
    });

    expect(res.statusCode).toBe(200);
    expect(res.headers['content-type']).toMatch(/^application\/json(?:; charset=utf-8)?$/);
    expect(res.headers['content-language']).toBe('zh-cn');
    expect(res.headers['x-selected-representation']).toBe('welcome-json-zh');
    const body = JSON.parse(res.body) as { title: string };
    expect(body.title).toBe('欢迎');
  });

  it('honors q weights across media and falls back zh request to zh-cn candidate (prefix)', async () => {
    const { app } = await startApp();
    const res = await app.inject({
      method: 'GET',
      url: '/welcome',
      headers: { Accept: 'text/html;q=0.4, application/json;q=0.9', 'Accept-Language': 'zh;q=1' },
    });
    expect(res.statusCode).toBe(200);
    expect(res.headers['x-selected-representation']).toBe('welcome-json-zh');
  });

  it('matches the text/plain representation only when the charset parameter satisfies', async () => {
    const { app } = await startApp();
    const ok = await app.inject({
      method: 'GET',
      url: '/welcome',
      headers: { Accept: 'text/plain;charset=utf-8', 'Accept-Language': 'en' },
    });
    expect(ok.statusCode).toBe(200);
    expect(ok.headers['content-type']).toBe('text/plain; charset=utf-8');

    const badCharset = await app.inject({
      method: 'GET',
      url: '/welcome',
      headers: { Accept: 'text/plain;charset=iso-8859-1', 'Accept-Language': 'en' },
    });
    expect(badCharset.statusCode).toBe(406);
    expect(JSON.parse(badCharset.body).error.category).toBe('MEDIA_NOT_ACCEPTABLE');
  });
});

describe('GET resource — explicit, categorized failures (never folded into success)', () => {
  it('returns 406 MEDIA_FORBIDDEN when every media type carries q=0', async () => {
    const { app } = await startApp();
    const res = await app.inject({
      method: 'GET',
      url: '/report',
      headers: { Accept: 'application/json;q=0, application/csv;q=0', 'Accept-Language': '*' },
    });
    expect(res.statusCode).toBe(406);
    const payload = JSON.parse(res.body) as { error: { category: string } };
    expect(payload.error.category).toBe('MEDIA_FORBIDDEN');
  });

  it('negotiates media and language independently: 406 LANGUAGE_NOT_ACCEPTABLE', async () => {
    const { app } = await startApp();
    const res = await app.inject({
      method: 'GET',
      url: '/report',
      headers: { Accept: 'application/json', 'Accept-Language': 'de' },
    });
    expect(res.statusCode).toBe(406);
    expect(JSON.parse(res.body).error.category).toBe('LANGUAGE_NOT_ACCEPTABLE');
  });

  it('406 LANGUAGE_FORBIDDEN when the offered language is pinned q=0', async () => {
    const { app } = await startApp();
    const res = await app.inject({
      method: 'GET',
      url: '/report',
      headers: { Accept: 'application/json', 'Accept-Language': 'en;q=0, fr;q=0' },
    });
    expect(res.statusCode).toBe(406);
    expect(JSON.parse(res.body).error.category).toBe('LANGUAGE_FORBIDDEN');
  });

  it('returns 400 MALFORMED_HEADER when the reject-header policy is configured', async () => {
    const { app } = await startApp({ invalidEntryPolicy: 'reject-header' });
    const res = await app.inject({
      method: 'GET',
      url: '/welcome',
      headers: { Accept: 'text/html;q=7' },
    });
    expect(res.statusCode).toBe(400);
    expect(JSON.parse(res.body).error.category).toBe('MALFORMED_HEADER');
  });

  it('returns 400 HEADER_TOO_LARGE for oversized negotiation lists', async () => {
    const { app } = await startApp({ maxAcceptEntries: 3 });
    const res = await app.inject({
      method: 'GET',
      url: '/welcome',
      headers: { Accept: 'a/1, b/2, c/3, d/4' },
    });
    expect(res.statusCode).toBe(400);
    expect(JSON.parse(res.body).error.category).toBe('HEADER_TOO_LARGE');
  });

  it('returns 404 NOT_FOUND for unknown resources with a run id', async () => {
    const { app } = await startApp();
    const res = await app.inject({ method: 'GET', url: '/missing' });
    expect(res.statusCode).toBe(404);
    expect(JSON.parse(res.body).error.category).toBe('NOT_FOUND');
    expect(res.headers['x-run-id']).toBeTruthy();
  });
});

describe('Vary and cache-dimension behavior', () => {
  it('sends Vary with both dimensions on a wildcard request even though nothing was constrained', async () => {
    const { app } = await startApp();
    const res = await app.inject({
      method: 'GET',
      url: '/welcome',
      headers: { Accept: '*/*', 'Accept-Language': '*' },
    });
    expect(res.statusCode).toBe(200);
    const vary = (res.headers['vary'] as string).split(',').map((v) => v.trim());
    expect(vary).toEqual(expect.arrayContaining(['Accept', 'Accept-Language']));
  });

  it('omits a dimension from Vary when the resource is uniform along it (/report language still varies)', async () => {
    const { app } = await startApp();
    // /report has json(en,fr) and csv(en): both dimensions vary.
    const res = await app.inject({ method: 'GET', url: '/report' });
    const vary = (res.headers['vary'] as string).split(',').map((v) => v.trim());
    expect(vary).toEqual(['Accept', 'Accept-Language']);
  });
});

describe('unified malformed-input policy over real HTTP', () => {
  it('drops an invalid-q item with a warning but serves a valid later item', async () => {
    const { app } = await startApp();
    const res = await app.inject({
      method: 'GET',
      url: '/welcome',
      headers: { Accept: 'text/html;q=2, application/json', 'Accept-Language': 'en' },
    });
    expect(res.statusCode).toBe(200);
    expect(res.headers['x-selected-representation']).toBe('welcome-json-en');
    const runId = res.headers['x-run-id'] as string;

    const diag = await app.inject({ method: 'GET', url: `/diagnostics?runId=${runId}` });
    const payload = JSON.parse(diag.body) as { runs: Array<{ warnings: Array<{ code: string }> }> };
    expect(payload.runs).toHaveLength(1);
    expect(payload.runs[0]!.warnings.map((w) => w.code)).toContain('INVALID_Q');
  });
});

describe('diagnostics endpoint and structured, correlatable run logs', () => {
  it('records the run with inputs, per-candidate steps and influence flags, retrievable by run id', async () => {
    const { app } = await startApp();
    const res = await app.inject({
      method: 'GET',
      url: '/welcome',
      headers: { Accept: 'application/json', 'Accept-Language': 'zh-cn' },
    });
    const runId = res.headers['x-run-id'] as string;

    const diag = await app.inject({ method: 'GET', url: `/diagnostics?runId=${runId}` });
    expect(diag.statusCode).toBe(200);
    const payload = JSON.parse(diag.body) as {
      runs: Array<{
        runId: string;
        requestHeaders: { accept: string; acceptLanguage: string };
        selectedId: string;
        vary: string[];
        traces: Array<{ candidateId: string; feasible: boolean; steps: string[] }>;
      }>;
    };
    expect(payload.runs).toHaveLength(1);
    const run = payload.runs[0]!;
    expect(run.runId).toBe(runId);
    expect(run.requestHeaders).toMatchObject({ accept: 'application/json', acceptLanguage: 'zh-cn' });
    expect(run.selectedId).toBe('welcome-json-zh');
    expect(run.vary).toEqual(expect.arrayContaining(['Accept', 'Accept-Language']));
    // Explainability: the forbidden/non-matching candidates carry concrete reasons.
    const htmlEn = run.traces.find((t) => t.candidateId === 'welcome-html-en')!;
    expect(htmlEn.feasible).toBe(false);
    expect(htmlEn.steps.join(' ')).toMatch(/media/);
  });

  it('emits JSON log lines sharing the runId, carrying version, progress steps and a basis', async () => {
    const { app } = await startApp();
    const res = await app.inject({
      method: 'GET',
      url: '/welcome',
      headers: { Accept: 'text/html', 'Accept-Language': 'zh-cn' },
    });
    expect(res.statusCode).toBe(200);
    const runId = res.headers['x-run-id'] as string;

    const records = logs.map((line) => JSON.parse(line) as Record<string, unknown>);
    expect(records.length).toBeGreaterThan(3);
    expect(records.every((r) => r.runId === runId)).toBe(true);
    expect(records.every((r) => typeof r.version === 'string')).toBe(true);
    expect(records[0]!.service).toBe('opp408-negotiation-service');

    const steps = records.map((r) => r.step) as number[];
    expect([...steps].sort((a, b) => a - b)).toEqual(steps); // monotonic progress
    const decide = records.find((r) => r.phase === 'decide')!;
    expect(decide.basis).toBe('negotiation decision');
    expect(decide.selectedId).toBe('welcome-html-zh');
  });

  it('logs an explicit error level with the failure category on a 406', async () => {
    const { app } = await startApp();
    const res = await app.inject({
      method: 'GET',
      url: '/welcome',
      headers: { Accept: 'image/png', 'Accept-Language': 'en' },
    });
    expect(res.statusCode).toBe(406);
    const errorRecords = logs
      .map((line) => JSON.parse(line) as Record<string, unknown>)
      .filter((r) => r.level === 'error');
    expect(errorRecords).toHaveLength(1);
    expect(errorRecords[0]!.failureCategory).toBe('MEDIA_NOT_ACCEPTABLE');
  });
});
