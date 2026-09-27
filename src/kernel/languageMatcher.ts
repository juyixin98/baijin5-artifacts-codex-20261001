/**
 * Language matching with explicit fallback — part of the negotiation kernel.
 *
 * RFC 4647 basic-filtering matches (range is a prefix of the candidate tag):
 *   - exact:    range subtags === candidate subtags
 *   - prefix:   range is a strict prefix (range "en" covers "en-gb")
 *   - wildcard: range "*" (lowest possible specificity)
 * When several ranges filter-match one candidate, the LONGEST matching
 * range wins (symmetric with media-type specificity), header position
 * breaking ties. Its q is the effective weight, so q=0 forbids.
 *
 * Extended fallback (non-standard, config-gated): when no range
 * filter-matches, a candidate that is a strict prefix of a range
 * (candidate "zh" vs range "zh-cn") may be selected with the weight
 * discounted once per stripped subtag. Fallback NEVER extends a q=0
 * forbiddance upward to a broader candidate.
 */

import type { Candidate, LanguageRangeEntry } from '../contract/types.js';

export type LanguageMatchKind = 'exact' | 'prefix' | 'fallback' | 'wildcard' | 'none';

export interface LanguageMatchResult {
  matched: boolean;
  range: LanguageRangeEntry | null;
  q: number;
  kind: LanguageMatchKind;
  /** Number of request subtags dropped during fallback (0 unless fallback). */
  strippedSubtags: number;
}

export interface LanguageFallbackConfig {
  enabled: boolean;
  penaltyPerStrippedSubtag: number;
}

export const DEFAULT_LANGUAGE_FALLBACK: LanguageFallbackConfig = {
  enabled: true,
  penaltyPerStrippedSubtag: 0.9,
};

function commonPrefixLength(a: string[], b: string[]): number {
  let n = 0;
  while (n < a.length && n < b.length && a[n] === b[n]) n++;
  return n;
}

export function matchLanguage(
  candidate: Candidate,
  entries: LanguageRangeEntry[],
  fallback: LanguageFallbackConfig = DEFAULT_LANGUAGE_FALLBACK,
): LanguageMatchResult {
  const tagSubtags = candidate.language.split('-');

  // Phase 1: RFC filtering matches — longest range prefix wins, '*' last.
  let filterWinner: LanguageMatchResult | null = null;
  // Phase 2: fallback candidates, considered only if phase 1 finds nothing.
  let fallbackWinner: LanguageMatchResult | null = null;

  for (const entry of entries) {
    if (entry.range === '*') {
      const candidateResult: LanguageMatchResult = {
        matched: true,
        range: entry,
        q: entry.q,
        kind: 'wildcard',
        strippedSubtags: 0,
      };
      if (filterWinner === null || filterWinner.kind === 'wildcard') {
        if (filterWinner === null || entry.position < filterWinner.range!.position) filterWinner = candidateResult;
      }
      continue;
    }

    const common = commonPrefixLength(tagSubtags, entry.subtags);
    if (common === entry.subtags.length) {
      const kind: LanguageMatchKind = common === tagSubtags.length ? 'exact' : 'prefix';
      const candidateResult: LanguageMatchResult = {
        matched: true,
        range: entry,
        q: entry.q,
        kind,
        strippedSubtags: 0,
      };
      if (
        filterWinner === null ||
        filterWinner.kind === 'wildcard' ||
        entry.subtags.length > filterWinner.range!.subtags.length ||
        (entry.subtags.length === filterWinner.range!.subtags.length && entry.position < filterWinner.range!.position)
      ) {
        filterWinner = candidateResult;
      }
      continue;
    }

    // Candidate is a strict prefix of the requested range: discounted fallback.
    if (fallback.enabled && common === tagSubtags.length && entry.q > 0) {
      const stripped = entry.subtags.length - common;
      const q = Number((entry.q * fallback.penaltyPerStrippedSubtag ** stripped).toFixed(6));
      const candidateResult: LanguageMatchResult = {
        matched: true,
        range: entry,
        q,
        kind: 'fallback',
        strippedSubtags: stripped,
      };
      if (
        fallbackWinner === null ||
        candidateResult.q > fallbackWinner.q ||
        (candidateResult.q === fallbackWinner.q && entry.position < fallbackWinner.range!.position)
      ) {
        fallbackWinner = candidateResult;
      }
    }
  }

  return filterWinner ?? fallbackWinner ?? { matched: false, range: null, q: 0, kind: 'none', strippedSubtags: 0 };
}
