import { describe, expect, it } from 'vitest';
import { parseAccept } from '../src/contract/acceptParser.js';
import { DEFAULT_POLICY } from '../src/contract/parsePolicy.js';

describe('parseAccept — q-value strictness (rule: q=0 legal, bad q rejected)', () => {
  it('keeps q=0 as an explicit, non-zero-position forbid entry', () => {
    const { entries, warnings, rejected } = parseAccept('text/html;q=0');
    expect(rejected).toBe(false);
    expect(entries).toHaveLength(1);
    expect(entries[0]!.q).toBe(0);
    expect(entries[0]!.type).toBe('text');
    expect(entries[0]!.subtype).toBe('html');
    expect(warnings).toEqual([]);
  });

  it('rejects q above 1, q=NaN-ish and q with >3 decimals as INVALID_Q, dropping the item', () => {
    for (const bad of ['text/html;q=1.5', 'text/html;q=abc', 'text/html;q=0.1234', 'text/html;q=1.001', 'text/html;q=']) {
      const { entries, warnings, rejected } = parseAccept(bad);
      expect(rejected).toBe(false);
      expect(entries).toEqual([]);
      expect(warnings.map((w) => w.code)).toContain('INVALID_Q');
      expect(warnings[0]!.item).toBe(bad);
    }
  });

  it('accepts the boundary qvalues 0, 0.000, 0.999, 1, 1.0, 1.000', () => {
    const { entries, warnings } = parseAccept('a/b;q=0, c/d;q=0.000, e/f;q=0.999, g/h;q=1, i/j;q=1.0, k/l;q=1.000');
    expect(warnings).toEqual([]);
    expect(entries.map((e) => e.q)).toEqual([0, 0, 0.999, 1, 1, 1]);
  });
});

describe('parseAccept — malformed entries and unified policy', () => {
  it('drops malformed ranges under drop-with-warning and continues', () => {
    const { entries, warnings, rejected } = parseAccept('text, application/json, */json, te xt/x');
    expect(rejected).toBe(false);
    expect(entries.map((e) => e.raw)).toEqual(['application/json']);
    expect(warnings.map((w) => w.code)).toEqual([
      'MALFORMED_ENTRY',
      'MALFORMED_ENTRY',
      'MALFORMED_ENTRY',
    ]);
  });

  it('rejects the whole header under reject-header policy (fail closed)', () => {
    const result = parseAccept('text/html;q=2, application/json', {
      ...DEFAULT_POLICY,
      invalidEntryPolicy: 'reject-header',
    });
    expect(result.rejected).toBe(true);
    expect(result.entries).toEqual([]);
    expect(result.warnings[0]!.code).toBe('INVALID_Q');
  });

  it('silently drops under ignore policy with no warnings', () => {
    const result = parseAccept('text/html;q=2, application/json', {
      ...DEFAULT_POLICY,
      invalidEntryPolicy: 'ignore',
    });
    expect(result.rejected).toBe(false);
    expect(result.entries.map((e) => e.raw)).toEqual(['application/json']);
    expect(result.warnings).toEqual([]);
  });

  it('flags duplicate parameters within one item (including a second q)', () => {
    const result = parseAccept('text/html;q=0.5;q=0.9');
    expect(result.entries).toEqual([]);
    expect(result.warnings[0]!.code).toBe('MALFORMED_PARAMETER');
  });
});

describe('parseAccept — duplicate entries', () => {
  it('first-wins keeps the first q and warns', () => {
    const { entries, warnings } = parseAccept('text/html;q=0.3, text/html;q=0.9');
    expect(entries).toHaveLength(1);
    expect(entries[0]!.q).toBe(0.3);
    expect(warnings.map((w) => w.code)).toEqual(['DUPLICATE_ENTRY']);
  });

  it('last-wins replaces the earlier entry', () => {
    const { entries } = parseAccept('text/html;q=0.3, text/html;q=0.9', {
      ...DEFAULT_POLICY,
      duplicatePolicy: 'last-wins',
    });
    expect(entries).toHaveLength(1);
    expect(entries[0]!.q).toBe(0.9);
    expect(entries[0]!.position).toBe(2);
  });

  it('treats different Accept-params as distinct entries', () => {
    const { entries, warnings } = parseAccept('text/plain;charset=utf-8, text/plain;charset=iso-8859-1');
    expect(entries).toHaveLength(2);
    expect(warnings).toEqual([]);
  });
});

describe('parseAccept — wildcards, positions and quoted values', () => {
  const result = parseAccept('text/*;q=0.7, */*;q=0.1, text/html;level=1;q=0.9');
  it('parses wildcard shapes and preserves stable positions', () => {
    expect(result.entries.map((e) => [e.type, e.subtype, e.q, e.position])).toEqual([
      ['text', '*', 0.7, 1],
      ['*', '*', 0.1, 2],
      ['text', 'html', 0.9, 3],
    ]);
  });
  it('lowercases accept-params and keeps them for matching', () => {
    expect(result.entries[2]!.params).toEqual({ level: '1' });
  });
  it('does not split on a comma inside a quoted parameter value', () => {
    const quoted = parseAccept('text/html;note="a,b", application/json');
    expect(quoted.entries).toHaveLength(2);
    expect(quoted.entries[0]!.params.note).toBe('a,b');
  });
  it('returns no entries for absent or whitespace-only headers', () => {
    expect(parseAccept(undefined).entries).toEqual([]);
    expect(parseAccept('   ').entries).toEqual([]);
  });
});
