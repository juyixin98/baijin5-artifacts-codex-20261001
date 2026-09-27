/**
 * Diagnostics trace store: a bounded, in-memory ring buffer.
 *
 * Every negotiation run is recorded with its runId, inputs, decision steps
 * and outcome so the diagnostics endpoint can explain PAST requests, not
 * just the current one. Oldest entries are evicted at capacity.
 */

import type { NegotiationResult } from '../kernel/negotiator.js';

export interface TraceRecord {
  runId: string;
  timestamp: string;
  method: string;
  url: string;
  resourcePath: string;
  requestHeaders: { accept: string | undefined; acceptLanguage: string | undefined };
  status: number;
  failureCategory: NegotiationResult['failureCategory'];
  selectedId: string | null;
  vary: string[];
  result: NegotiationResult;
}

export class TraceStore {
  private readonly records: TraceRecord[] = [];

  constructor(private readonly capacity: number) {}

  add(record: TraceRecord): void {
    this.records.push(record);
    while (this.records.length > this.capacity) this.records.shift();
  }

  /** Ordered newest-first; optionally filtered by runId. */
  list(runId?: string): TraceRecord[] {
    const all = [...this.records].reverse();
    return runId ? all.filter((record) => record.runId === runId) : all;
  }

  get size(): number {
    return this.records.length;
  }
}
