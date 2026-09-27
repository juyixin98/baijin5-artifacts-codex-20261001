/**
 * Kernel tests over HAND-COMPUTED candidate sets.
 *
 * The expectations below (selected id, q, failure category) were worked
 * out independently of the implementation. The candidate set deliberately
 * mixes media types, params, languages and ordinals so specificity,
 * wildcards, q=0 forbiddance and language fallback are all exercised.
 *
 * Candidates (declaration ordinal in brackets):
 *   c1 text/html       en      [0]
 *   c2 text/html       zh-cn   [1]
 *   c3 application/json en     [2]
 *   c4 application/json zh-cn  [3]
 *   c5 text/plain;charset=utf-8 en [4]
 */

import { describe, expect, it } from 'vitest';
import { negotiate, resourceVaryDimensions } from '../src/kernel/negotiator.js';
import { parseAccept } from '../src/contract/acceptParser.js';
import { parseAcceptLanguage } from '../src/contract/languageParser.js';
import { DEFAULT_POLICY, type ParsePolicy } from '../src/contract/parsePolicy.js';
import type { Candidate } from '../src/contract/types.js';
import { DEFAULT_LANGUAGE_FALLBACK, type LanguageFallbackConfig } from '../src/kernel/languageMatcher.js';

function candidate(
  id: string,
  mediaType: string,
  language: string,
  ordinal: number,
  params: Record<string, string> = {},
): Candidate {
  const [type, subtype] = mediaType.split('/') as [string, string];
  return { id, type, subtype, params, language, ordinal };
}

const CANDS: Candidate[] = [
  candidate('c1', 'text/html', 'en', 0),
  candidate('c2', 'text/html', 'zh-cn', 1),
  candidate('c3', 'application/json', 'en', 2),
  candidate('c4', 'application/json', 'zh-cn', 3),
  candidate('c5', 'text/plain', 'en', 4, { charset: 'utf-8' }),
];

function decide(
  cands: Candidate[],
  headers: { accept?: string; acceptLanguage?: string },
  fallback: LanguageFallbackConfig = DEFAULT_LANGUAGE_FALLBACK,
  policy: ParsePolicy = DEFAULT_POLICY,
) {
  const accept = parseAccept(headers.accept, policy);
  const languages = parseAcceptLanguage(headers.acceptLanguage, policy);
  return negotiate(
    {
      accept: accept.entries,
      languages: languages.entries,
      candidates: cands,
      warnings: [...accept.warnings, ...languages.warnings],
      acceptRejected: accept.rejected,
      languageRejected: languages.rejected,
    },
    fallback,
  );
}

describe('media negotiation — wildcard specificity and q=0 forbiddance', () => {
  it('forbids every candidate when all exact types carry q=0 (no wildcard present)', () => {
    const r = decide(CANDS, { accept: 'application/json;q=0, text/html;q=0, text/plain;q=0', acceptLanguage: '*' });
    expect(r.failureCategory).toBe('MEDIA_FORBIDDEN');
    expect(r.httpStatus).toBe(406);
    expect(r.traces.every((t) => !t.feasible)).toBe(true);
    expect(r.traces.filter((t) => t.media.forbiddenByZero)).toHaveLength(5);
  });

  it('forbids via type wildcards: text/*;q=0 and application/*;q=0 cover all candidates', () => {
    const r = decide(CANDS, { accept: 'text/*;q=0, application/*;q=0', acceptLanguage: '*' });
    expect(r.failureCategory).toBe('MEDIA_FORBIDDEN');
    expect(r.scores).toEqual([]);
  });

  it('lets the most SPECIFIC range decide even at q=0: json exact q=0 beats application/*;q=0.5 and */*;q=0.9', () => {
    // Hand-computed: c3/c4 forbidden (exact q=0). c1,c2,c5 survive via
    // */* at q=0.9; tie broken by ordinal -> c1.
    const r = decide(CANDS, { accept: 'application/json;q=0, application/*;q=0.5, */*;q=0.9', acceptLanguage: '*' });
    expect(r.selected!.id).toBe('c1');
    const c3 = r.traces.find((t) => t.candidateId === 'c3')!;
    expect(c3.media.forbiddenByZero).toBe(true);
    expect(c3.media.matchedRange).toBe('application/json;q=0');
    expect(r.scores.map((s) => s.candidateId)).toEqual(['c1', 'c2', 'c5']);
  });

  it('applies specificity over q for text/plain;q=0 under broader positive wildcards', () => {
    // c5 forbidden exactly; html via text/* = 0.5; json via */* = 1 -> c3.
    const r = decide(CANDS, { accept: 'text/plain;q=0, text/*;q=0.5, */*;q=1', acceptLanguage: '*' });
    expect(r.selected!.id).toBe('c3');
    expect(r.traces.find((t) => t.candidateId === 'c5')!.feasible).toBe(false);
    expect(r.scores.find((s) => s.candidateId === 'c1')!.mediaQ).toBe(0.5);
    expect(r.scores.find((s) => s.candidateId === 'c3')!.mediaQ).toBe(1);
  });
});

describe('media negotiation — weights, parameters and stable ties', () => {
  it('picks higher q over ordinal', () => {
    const r = decide(CANDS, { accept: 'text/html;q=0.5, application/json;q=0.9', acceptLanguage: '*' });
    expect(r.selected!.id).toBe('c3');
  });

  it('breaks equal-weight ties by declaration ordinal, independent of header order', () => {
    const a = decide(CANDS, { accept: 'text/html, application/json', acceptLanguage: '*' });
    const b = decide(CANDS, { accept: 'application/json, text/html', acceptLanguage: '*' });
    expect(a.selected!.id).toBe('c1');
    expect(b.selected!.id).toBe('c1');
  });

  it('matches a required Accept-param and prefers the more specific range despite lower q', () => {
    // Param range specificity 3.01 beats bare text/plain (3.00): c5 at 0.5,
    // and it is the only feasible type -> c5.
    const r = decide(CANDS, { accept: 'text/plain;charset=utf-8;q=0.5, text/plain;q=0.9', acceptLanguage: '*' });
    expect(r.selected!.id).toBe('c5');
    expect(r.scores[0]!.mediaQ).toBe(0.5);
  });

  it('a non-satisfiable param range does not match, and the candidate falls through to */*', () => {
    const r = decide(CANDS, {
      accept: 'text/plain;charset=iso-8859-1;q=1, */*;q=0.2',
      acceptLanguage: '*',
    });
    const c5 = r.traces.find((t) => t.candidateId === 'c5')!;
    expect(c5.media.matchedRange).toBe('*/*;q=0.2');
    expect(c5.media.failedParamChecks.find((p) => p.name === 'charset')!.matched).toBe(false);
    expect(c5.feasible).toBe(true);
    expect(r.selected!.id).toBe('c1'); // all survivors tie at 0.2 -> ordinal
  });
});

describe('language negotiation — exact, prefix, wildcard and q=0', () => {
  it('prefers the higher-weighted exact zh-cn representations and resolves the media tie by ordinal', () => {
    // Distinct language weights so language (not server ordinal) decides;
    // within zh-cn (0.9) the media tie falls through to ordinal.
    const r = decide(CANDS, { accept: '*/*', acceptLanguage: 'zh-CN;q=0.9, en;q=0.8' });
    expect(r.selected!.id).toBe('c2'); // html zh-cn (ordinal 1) vs json zh-cn (ordinal 3)
    expect(r.scores.map((s) => [s.candidateId, s.languageQ])).toEqual([
      ['c2', 0.9],
      ['c4', 0.9],
      ['c1', 0.8],
      ['c3', 0.8],
      ['c5', 0.8],
    ]);
  });

  it('falls back to declaration ordinal across dimensions when every weight ties', () => {
    const r = decide(CANDS, { accept: '*/*', acceptLanguage: 'zh-CN,en' });
    expect(r.selected!.id).toBe('c1');
    expect(r.scores.every((s) => s.mediaQ === 1 && s.languageQ === 1)).toBe(true);
  });

  it('classifies LANGUAGE_FORBIDDEN when every language is pinned to q=0 (specificity over wildcard)', () => {
    // en and zh-cn exact q=0 outrank the positive wildcard for those tags.
    const r = decide(CANDS, { accept: '*/*;q=1', acceptLanguage: 'en;q=0, zh-cn;q=0, *;q=0.5' });
    expect(r.failureCategory).toBe('LANGUAGE_FORBIDDEN');
    expect(r.traces.every((t) => t.languageResult.forbiddenByZero || !t.languageResult.matched)).toBe(true);
  });

  it('q=0 on en forbids only en; zh-cn survives via the wildcard (exact 0 outranks * for en)', () => {
    const r = decide(CANDS, { accept: '*/*', acceptLanguage: 'en;q=0, *;q=0.5' });
    expect(r.selected!.id).toBe('c2');
    expect(r.scores.map((s) => s.candidateId)).toEqual(['c2', 'c4']);
    expect(r.traces.find((t) => t.candidateId === 'c1')!.languageResult.forbiddenByZero).toBe(true);
  });

  it('does not extend a q=0 on a LONGER range up to a shorter candidate (no fallback forbiddance)', () => {
    // en-US;q=0 must not forbid plain en; with no other range en simply
    // does not match and zh-cn does not match either -> not acceptable.
    const r = decide(CANDS, { accept: '*/*', acceptLanguage: 'en-US;q=0' });
    expect(r.failureCategory).toBe('LANGUAGE_NOT_ACCEPTABLE');
    expect(r.traces.find((t) => t.candidateId === 'c1')!.languageResult.forbiddenByZero).toBe(false);
  });
});

describe('language negotiation — discounted fallback for stripped subtags', () => {
  it('matches en candidate for en-US request as fallback, q discounted once (0.9 -> 0.81)', () => {
    const r = decide(CANDS, { accept: '*/*', acceptLanguage: 'en-US;q=0.9' });
    expect(r.selected!.id).toBe('c1');
    const c1 = r.traces.find((t) => t.candidateId === 'c1')!;
    expect(c1.languageResult.kind).toBe('fallback');
    expect(c1.languageResult.strippedSubtags).toBe(1);
    expect(c1.languageResult.q).toBeCloseTo(0.81, 10);
    // zh-cn candidates cannot match an en-* range at all.
    expect(r.traces.find((t) => t.candidateId === 'c2')!.feasible).toBe(false);
  });

  it('prefix matches (short range, long candidate) carry FULL weight and kind "prefix"', () => {
    const r = decide(CANDS, { accept: 'application/json', acceptLanguage: 'zh;q=0.6' });
    expect(r.selected!.id).toBe('c4'); // json zh-cn via prefix
    const c4 = r.traces.find((t) => t.candidateId === 'c4')!;
    expect(c4.languageResult.kind).toBe('prefix');
    expect(c4.languageResult.q).toBe(0.6);
  });

  it('disables fallback from configuration, yielding an explicit language failure', () => {
    const r = decide(
      CANDS,
      { accept: '*/*', acceptLanguage: 'en-US;q=0.9' },
      { enabled: false, penaltyPerStrippedSubtag: 0.9 },
    );
    expect(r.failureCategory).toBe('LANGUAGE_NOT_ACCEPTABLE');
  });
});

describe('independent dimensions, missing headers, and failure categories', () => {
  it('fails media independently with MEDIA_NOT_ACCEPTABLE even though a language is offered', () => {
    const r = decide(CANDS, { accept: 'image/webp', acceptLanguage: 'en' });
    expect(r.failureCategory).toBe('MEDIA_NOT_ACCEPTABLE');
    expect(r.httpStatus).toBe(406);
  });

  it('fails language independently with LANGUAGE_NOT_ACCEPTABLE when media matches', () => {
    const r = decide(CANDS, { accept: 'application/json', acceptLanguage: 'fr' });
    expect(r.failureCategory).toBe('LANGUAGE_NOT_ACCEPTABLE');
    expect(r.scores).toEqual([]);
  });

  it('reports NO_CANDIDATES for an empty representation set', () => {
    const r = decide([], { accept: '*/*', acceptLanguage: '*' });
    expect(r.failureCategory).toBe('NO_CANDIDATES');
    expect(r.selected).toBeNull();
  });

  it('fails closed with MALFORMED_HEADER under the reject-header policy', () => {
    const r = decide(
      CANDS,
      { accept: 'text/html;q=7', acceptLanguage: '*' },
      DEFAULT_LANGUAGE_FALLBACK,
      { ...DEFAULT_POLICY, invalidEntryPolicy: 'reject-header' },
    );
    expect(r.failureCategory).toBe('MALFORMED_HEADER');
    expect(r.httpStatus).toBe(400);
  });

  it('treats missing headers as neutral: selects the first candidate but still marks vary dimensions', () => {
    const r = decide(CANDS, {});
    expect(r.selected!.id).toBe('c1');
    expect(r.vary).toEqual(['Accept', 'Accept-Language']);
    expect(r.influencedHeaders).toEqual({ accept: false, acceptLanguage: false });
  });

  it('marks a wildcard header as non-influencing while keep it in vary (cache-key correctness)', () => {
    const r = decide(CANDS, { accept: '*/*', acceptLanguage: '*' });
    expect(r.selected!.id).toBe('c1');
    expect(r.influencedHeaders.accept).toBe(false);
    expect(r.influencedHeaders.acceptLanguage).toBe(false);
    expect(r.vary).toContain('Accept');
    expect(r.vary).toContain('Accept-Language');
  });

  it('flags the header that actually moved the decision', () => {
    const r = decide(CANDS, { accept: 'application/json', acceptLanguage: '*' });
    expect(r.selected!.id).toBe('c3');
    expect(r.influencedHeaders.accept).toBe(true);
    expect(r.influencedHeaders.acceptLanguage).toBe(false);
  });
});

describe('resource-level cache dimensions', () => {
  it('lists only dimensions on which candidates actually differ', () => {
    const mediaOnly = [candidate('a', 'text/html', 'en', 0), candidate('b', 'application/json', 'en', 1)];
    const langOnly = [candidate('a', 'application/json', 'en', 0), candidate('b', 'application/json', 'fr', 1)];
    const uniform = [candidate('a', 'application/json', 'en', 0)];
    expect(resourceVaryDimensions(mediaOnly)).toEqual(['Accept']);
    expect(resourceVaryDimensions(langOnly)).toEqual(['Accept-Language']);
    expect(resourceVaryDimensions(uniform)).toEqual([]);
  });
});

describe('trace explanations', () => {
  it('records ordered human-readable steps including the judgement basis', () => {
    const r = decide(CANDS, { accept: 'text/html;q=0, application/json', acceptLanguage: 'zh-cn' });
    const c1 = r.traces.find((t) => t.candidateId === 'c1')!;
    expect(c1.steps.join('\n')).toMatch(/q=0/);
    expect(c1.steps.at(-1)).toMatch(/REJECTED: media explicitly forbidden/);
    const c4 = r.traces.find((t) => t.candidateId === 'c4')!;
    expect(c4.steps.at(-1)).toMatch(/FEASIBLE/);
  });
});
