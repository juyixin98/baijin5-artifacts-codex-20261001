/**
 * Media-range matching — part of the negotiation kernel.
 *
 * Pure function over parsed contract types. No I/O, no HTTP types.
 *
 * Match semantics (documented because RFC 9110 leaves Accept-params loose):
 *   - the full wildcard range (star slash star) matches every media type
 *   - type-slash-star matches every subtype of that type
 *   - type-slash-subtype matches only that exact type/subtype
 *   - every Accept-param on the range must be present on the candidate
 *     with an equal (case-insensitive) value; the candidate MAY carry
 *     additional parameters
 *
 * Effective weight (RFC 9110 §12.5.1): when several ranges match one
 * candidate, the MOST SPECIFIC reference wins, regardless of q:
 *   exact type+subtype > type-slash-star > full wildcard; more required
 * Accept-params breaks further ties; header order is the final stable
 * tie-break. Consequence: text/plain at q=0 plus text-star at q=0.5
 * forbids text/plain.
 */

import type { AcceptEntry, Candidate } from '../contract/types.js';

export interface ParamCheck {
  name: string;
  expected: string;
  actual: string | undefined;
  matched: boolean;
}

export interface MediaMatchResult {
  matched: boolean;
  /** The winning range; null when nothing matched. */
  range: AcceptEntry | null;
  q: number;
  specificity: number;
  /** Param checks of the winning range. */
  paramChecks: ParamCheck[];
  /**
   * Failed param checks from ranges that matched type/subtype structurally
   * but were ruled out by a parameter — kept so the explanation can show
   * WHY a seemingly-applicable range did not win.
   */
  failedParamChecks: ParamCheck[];
}

export function specificityOf(entry: AcceptEntry): number {
  const base = entry.type === '*' ? 1 : entry.subtype === '*' ? 2 : 3;
  // Parameters add fractional specificity below the structural level.
  return base + Object.keys(entry.params).length * 0.01;
}

function paramsSatisfy(entry: AcceptEntry, candidate: Candidate): ParamCheck[] {
  return Object.entries(entry.params).map(([name, expected]) => {
    const actual = candidate.params[name];
    return { name, expected, actual, matched: actual === expected };
  });
}

/** Evaluate one candidate against ALL accepted ranges; specificity wins. */
export function matchMedia(candidate: Candidate, entries: AcceptEntry[]): MediaMatchResult {
  let winner: AcceptEntry | null = null;
  let winnerChecks: ParamCheck[] = [];
  const failedParamChecks: ParamCheck[] = [];

  for (const entry of entries) {
    const typeOk = entry.type === '*' || entry.type === candidate.type;
    const subtypeOk = entry.subtype === '*' || entry.subtype === candidate.subtype;
    if (!(typeOk && subtypeOk)) continue;

    const paramChecks = paramsSatisfy(entry, candidate);
    if (!paramChecks.every((check) => check.matched)) {
      // Structurally applicable but ruled out by a parameter mismatch.
      failedParamChecks.push(...paramChecks.filter((check) => !check.matched));
      continue;
    }

    if (winner === null) {
      winner = entry;
      winnerChecks = paramChecks;
      continue;
    }
    const takeOver =
      specificityOf(entry) > specificityOf(winner) ||
      (specificityOf(entry) === specificityOf(winner) && entry.position < winner.position);
    if (takeOver) {
      winner = entry;
      winnerChecks = paramChecks;
    }
  }

  if (winner === null) {
    return { matched: false, range: null, q: 0, specificity: 0, paramChecks: [], failedParamChecks };
  }
  return {
    matched: true,
    range: winner,
    q: winner.q,
    specificity: specificityOf(winner),
    paramChecks: winnerChecks,
    failedParamChecks,
  };
}
