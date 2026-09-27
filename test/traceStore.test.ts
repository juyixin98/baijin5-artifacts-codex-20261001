import { describe, expect, it } from 'vitest';
import { TraceStore, type TraceRecord } from '../src/state/traceStore.js';

function record(runId: string): TraceRecord {
  return {
    runId,
    timestamp: '2026-09-28T00:00:00.000Z',
    method: 'GET',
    url: '/welcome',
    resourcePath: '/welcome',
    requestHeaders: { accept: '*/*', acceptLanguage: '*' },
    status: 200,
    failureCategory: 'OK',
    selectedId: 'welcome-html-en',
    vary: ['Accept', 'Accept-Language'],
    result: {
      ok: true,
      httpStatus: 200,
      failureCategory: 'OK',
      failureMessage: 'representation selected',
      selected: null,
      scores: [],
      traces: [],
      warnings: [],
      vary: ['Accept', 'Accept-Language'],
      influencedHeaders: { accept: false, acceptLanguage: false },
      counterfactual: {
        withoutAccept: { selectedId: null, feasibleCount: 0 },
        withoutAcceptLanguage: { selectedId: null, feasibleCount: 0 },
      },
    },
  };
}

describe('TraceStore — bounded ring buffer with runId correlation', () => {
  it('evicts oldest records at capacity and lists newest-first', () => {
    const store = new TraceStore(2);
    store.add(record('a'));
    store.add(record('b'));
    store.add(record('c'));
    expect(store.size).toBe(2);
    expect(store.list().map((r) => r.runId)).toEqual(['c', 'b']);
  });

  it('filters by runId', () => {
    const store = new TraceStore(4);
    store.add(record('a'));
    store.add(record('b'));
    expect(store.list('a').map((r) => r.runId)).toEqual(['a']);
  });
});
