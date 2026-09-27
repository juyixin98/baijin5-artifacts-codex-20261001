import { describe, expect, it } from 'vitest';
import { parseAcceptLanguage } from '../src/contract/languageParser.js';
import { DEFAULT_POLICY } from '../src/contract/parsePolicy.js';

describe('parseAcceptLanguage — grammar and weights', () => {
  it('parses tags, wildcard, subtags, q and positions', () => {
    const { entries, warnings } = parseAcceptLanguage('zh-CN,en;q=0.8,*;q=0.1');
    expect(warnings).toEqual([]);
    expect(entries.map((e) => [e.range, e.q, e.position])).toEqual([
      ['zh-cn', 1, 1],
      ['en', 0.8, 2],
      ['*', 0.1, 3],
    ]);
    expect(entries[0]!.subtags).toEqual(['zh', 'cn']);
  });

  it('keeps q=0 as an explicit forbid range', () => {
    const { entries } = parseAcceptLanguage('fr;q=0, en;q=1');
    expect(entries.map((e) => [e.range, e.q])).toEqual([
      ['fr', 0],
      ['en', 1],
    ]);
  });

  it('rejects malformed tags and invalid q with the unified codes', () => {
    const badTag = parseAcceptLanguage('en-verylongsubtag, en');
    expect(badTag.entries.map((e) => e.range)).toEqual(['en']);
    expect(badTag.warnings[0]!.code).toBe('MALFORMED_ENTRY');

    const badQ = parseAcceptLanguage('en;q=2');
    expect(badQ.entries).toEqual([]);
    expect(badQ.warnings[0]!.code).toBe('INVALID_Q');
  });

  it('treats any non-q parameter as UNKNOWN_PARAMETER and drops the item', () => {
    const result = parseAcceptLanguage('en;level=1, fr');
    expect(result.entries.map((e) => e.range)).toEqual(['fr']);
    expect(result.warnings[0]!.code).toBe('UNKNOWN_PARAMETER');
  });

  it('applies reject-header and duplicate policies identically to Accept', () => {
    const rejected = parseAcceptLanguage('en;q=9, fr', { ...DEFAULT_POLICY, invalidEntryPolicy: 'reject-header' });
    expect(rejected.rejected).toBe(true);

    const dup = parseAcceptLanguage('en;q=0.3, en;q=0.9');
    expect(dup.entries).toHaveLength(1);
    expect(dup.entries[0]!.q).toBe(0.3);
    expect(dup.warnings[0]!.code).toBe('DUPLICATE_ENTRY');

    const lastWins = parseAcceptLanguage('en;q=0.3, en;q=0.9', { ...DEFAULT_POLICY, duplicatePolicy: 'last-wins' });
    expect(lastWins.entries[0]!.q).toBe(0.9);
  });

  it('neutralizes absent headers to zero entries (kernel interprets as wildcard)', () => {
    expect(parseAcceptLanguage(undefined).entries).toEqual([]);
    expect(parseAcceptLanguage('').entries).toEqual([]);
  });
});
