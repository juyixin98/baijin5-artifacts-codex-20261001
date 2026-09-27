/**
 * The negotiation kernel: combines the independently-parsed Accept and
 * Accept-Language contracts with media/language matchers into one
 * deterministic, explainable decision.
 *
 * Invariants enforced here (the acceptance rules of the task):
 *   1. q=0 explicitly forbids a candidate; it is never treated as "low q".
 *   2. Wildcard specificity and equal-weight ties are broken deterministically
 *      (media q desc, language q desc, declaration ordinal asc).
 *   3. Media and language are negotiated independently; failure to satisfy
 *      either dimension yields an explicit failure CATEGORY, not a guess.
 *   4. `vary` lists only headers whose presence actually changed feasibility
 *      or the selected candidate (counterfactual test).
 */

import type { AcceptEntry, Candidate, LanguageRangeEntry, ParseWarning } from '../contract/types.js';
import { matchMedia } from './mediaMatcher.js';
import { matchLanguage } from './languageMatcher.js';
import type { LanguageFallbackConfig } from './languageMatcher.js';

export type FailureCategory =
  | 'OK'
  | 'NO_CANDIDATES'
  | 'MALFORMED_HEADER'
  | 'HEADER_TOO_LARGE'
  | 'MEDIA_FORBIDDEN'
  | 'MEDIA_NOT_ACCEPTABLE'
  | 'LANGUAGE_FORBIDDEN'
  | 'LANGUAGE_NOT_ACCEPTABLE';

export interface CandidateTrace {
  candidateId: string;
  mediaType: string;
  language: string;
  ordinal: number;
  media: {
    matched: boolean;
    forbiddenByZero: boolean;
    q: number;
    specificity: number;
    matchedRange: string | null;
    paramChecks: { name: string; expected: string; actual: string | undefined; matched: boolean }[];
    failedParamChecks: { name: string; expected: string; actual: string | undefined; matched: boolean }[];
  };
  languageResult: {
    matched: boolean;
    forbiddenByZero: boolean;
    q: number;
    kind: 'exact' | 'prefix' | 'fallback' | 'wildcard' | 'none';
    matchedRange: string | null;
    strippedSubtags: number;
  };
  feasible: boolean;
  /** Ordered human-readable decision steps for this candidate. */
  steps: string[];
}

export interface NegotiationInput {
  accept: AcceptEntry[];
  languages: LanguageRangeEntry[];
  candidates: Candidate[];
  warnings: ParseWarning[];
  acceptRejected: boolean;
  languageRejected: boolean;
}

export interface NegotiationResult {
  ok: boolean;
  httpStatus: 200 | 400 | 406;
  failureCategory: FailureCategory;
  failureMessage: string;
  selected: Candidate | null;
  scores: { candidateId: string; mediaQ: number; languageQ: number; ordinal: number }[];
  traces: CandidateTrace[];
  warnings: ParseWarning[];
  /**
   * Headers the response must be cached on (RFC 9110 §12.5.2: every header
   * "subject to proactive negotiation"). Derived from the RESOURCE — a
   * header is listed iff the candidates actually differ along that
   * dimension — not from this particular request, so a wildcard request
   * still produces a keyed cache entry and cannot shadow others.
   */
  vary: string[];
  /**
   * Per-request influence: whether THIS request's header value changed
   * the decision vs. the neutral (absent-header) default. This is the
   * explainability counterpart of `vary`; it can be false while vary
   * still lists the header (e.g. Accept: *&#47;* on a multi-type resource).
   */
  influencedHeaders: { accept: boolean; acceptLanguage: boolean };
  counterfactual: {
    withoutAccept: { selectedId: string | null; feasibleCount: number };
    withoutAcceptLanguage: { selectedId: string | null; feasibleCount: number };
  };
}

interface ScoredCandidate {
  candidate: Candidate;
  mediaQ: number;
  languageQ: number;
  trace: CandidateTrace;
}

const NEUTRAL_ACCEPT: AcceptEntry[] = [
  { raw: '*/*', type: '*', subtype: '*', params: {}, q: 1, position: 1 },
];
const NEUTRAL_LANGUAGE: LanguageRangeEntry[] = [
  { raw: '*', range: '*', subtags: [], q: 1, position: 1 },
];

function score(
  input: NegotiationInput,
  accept: AcceptEntry[],
  languages: LanguageRangeEntry[],
  fallback: LanguageFallbackConfig,
  wantTraces: boolean,
): { scored: ScoredCandidate[]; traces: CandidateTrace[] } {
  const scored: ScoredCandidate[] = [];
  const traces: CandidateTrace[] = [];

  for (const candidate of input.candidates) {
    const steps: string[] = [];
    const media = matchMedia(candidate, accept);
    const mediaQ = media.matched ? media.q : 0;
    const mediaForbidden = media.matched && media.q === 0;
    steps.push(
      media.matched
        ? `media ${candidate.type}/${candidate.subtype} matched range "${media.range!.raw}" at q=${media.q}`
        : `media ${candidate.type}/${candidate.subtype} matched no Accept range`,
    );
    for (const check of media.paramChecks) {
      steps.push(
        check.matched
          ? `param ${check.name}=${check.expected} satisfied`
          : `param ${check.name}=${check.expected} NOT satisfied (actual: ${check.actual ?? 'absent'})`,
      );
    }
    for (const check of media.failedParamChecks) {
      steps.push(
        `range otherwise matched but param ${check.name}=${check.expected} NOT satisfied (actual: ${check.actual ?? 'absent'}); range not applicable`,
      );
    }

    const language = matchLanguage(candidate, languages, fallback);
    const languageQ = language.matched ? language.q : 0;
    const languageForbidden = language.matched && language.q === 0;
    steps.push(
      language.matched
        ? `language ${candidate.language} matched range "${language.range!.raw}" (${language.kind}${
            language.kind === 'fallback' ? `, stripped ${language.strippedSubtags} subtag(s), discounted q=${language.q}` : `, q=${language.q}`
          })`
        : `language ${candidate.language} matched no Accept-Language range`,
    );

    const feasible = !mediaForbidden && !languageForbidden && media.matched && language.matched;
    if (mediaForbidden) steps.push('=> REJECTED: media explicitly forbidden by q=0');
    else if (languageForbidden) steps.push('=> REJECTED: language explicitly forbidden by q=0');
    else if (!media.matched) steps.push('=> REJECTED: media type not acceptable');
    else if (!language.matched) steps.push('=> REJECTED: language not acceptable');
    else steps.push(`=> FEASIBLE (mediaQ=${mediaQ}, languageQ=${languageQ}, ordinal=${candidate.ordinal})`);

    const trace: CandidateTrace = {
      candidateId: candidate.id,
      mediaType: `${candidate.type}/${candidate.subtype}`,
      language: candidate.language,
      ordinal: candidate.ordinal,
      media: {
        matched: media.matched,
        forbiddenByZero: mediaForbidden,
        q: mediaQ,
        specificity: media.specificity,
        matchedRange: media.range?.raw ?? null,
        paramChecks: media.paramChecks,
        failedParamChecks: media.failedParamChecks,
      },
      languageResult: {
        matched: language.matched,
        forbiddenByZero: languageForbidden,
        q: languageQ,
        kind: language.kind,
        matchedRange: language.range?.raw ?? null,
        strippedSubtags: language.strippedSubtags,
      },
      feasible,
      steps,
    };
    traces.push(trace);
    if (feasible) scored.push({ candidate, mediaQ, languageQ, trace });
  }

  // Deterministic total order: media q, language q, then declaration ordinal.
  scored.sort((a, b) => {
    if (b.mediaQ !== a.mediaQ) return b.mediaQ - a.mediaQ;
    if (b.languageQ !== a.languageQ) return b.languageQ - a.languageQ;
    return a.candidate.ordinal - b.candidate.ordinal;
  });

  return { scored, traces: wantTraces ? traces : [] };
}

function classify(
  input: NegotiationInput,
  traces: CandidateTrace[],
): Pick<NegotiationResult, 'httpStatus' | 'failureCategory' | 'failureMessage'> {
  if (input.acceptRejected || input.languageRejected) {
    return {
      httpStatus: 400,
      failureCategory: 'MALFORMED_HEADER',
      failureMessage: 'A negotiation header was rejected by the configured parse policy; refusing to guess.',
    };
  }
  if (input.candidates.length === 0) {
    return {
      httpStatus: 406,
      failureCategory: 'NO_CANDIDATES',
      failureMessage: 'Resource has no representations to negotiate.',
    };
  }

  const mediaOk = traces.filter((trace) => trace.media.matched && !trace.media.forbiddenByZero);
  const mediaForbidden = traces.filter((trace) => trace.media.forbiddenByZero);
  if (mediaOk.length === 0) {
    if (mediaForbidden.length > 0) {
      return {
        httpStatus: 406,
        failureCategory: 'MEDIA_FORBIDDEN',
        failureMessage: `All ${mediaForbidden.length} candidate media type(s) are explicitly forbidden by q=0.`,
      };
    }
    return {
      httpStatus: 406,
      failureCategory: 'MEDIA_NOT_ACCEPTABLE',
      failureMessage: 'No candidate media type matches any non-zero Accept range.',
    };
  }

  const languageOk = traces.filter(
    (trace) => trace.media.matched && !trace.media.forbiddenByZero && trace.languageResult.matched && !trace.languageResult.forbiddenByZero,
  );
  const languageForbidden = traces.filter(
    (trace) => trace.media.matched && !trace.media.forbiddenByZero && trace.languageResult.forbiddenByZero,
  );
  if (languageOk.length === 0) {
    if (languageForbidden.length > 0) {
      return {
        httpStatus: 406,
        failureCategory: 'LANGUAGE_FORBIDDEN',
        failureMessage: `Every media-feasible candidate's language is explicitly forbidden by q=0.`,
      };
    }
    return {
      httpStatus: 406,
      failureCategory: 'LANGUAGE_NOT_ACCEPTABLE',
      failureMessage: 'No media-feasible candidate matches any non-zero Accept-Language range.',
    };
  }

  return { httpStatus: 200, failureCategory: 'OK', failureMessage: 'representation selected' };
}

/**
 * Resource-level Vary: which dimensions do the candidates actually vary
 * on? Media matters iff at least two distinct media types exist; language
 * matters iff at least two distinct languages exist. This is independent
 * of the incoming request, so wildcard and absent headers still produce
 * the keyed Vary that keeps caches correct.
 */
export function resourceVaryDimensions(candidates: Candidate[]): string[] {
  const vary: string[] = [];
  const mediaTypes = new Set(candidates.map((candidate) => `${candidate.type}/${candidate.subtype}`));
  const languages = new Set(candidates.map((candidate) => candidate.language));
  if (mediaTypes.size > 1) vary.push('Accept');
  if (languages.size > 1) vary.push('Accept-Language');
  return vary;
}

function toScores(scored: ScoredCandidate[]) {
  return scored.map((s) => ({
    candidateId: s.candidate.id,
    mediaQ: s.mediaQ,
    languageQ: s.languageQ,
    ordinal: s.candidate.ordinal,
  }));
}

export function negotiate(input: NegotiationInput, fallbackConfig: LanguageFallbackConfig): NegotiationResult {
  // RFC 9110: an ABSENT list accepts everything (server picks its preference).
  // Note: a present-but-rejected header is NOT neutralized — fail closed.
  const effectiveAccept = input.accept.length === 0 ? NEUTRAL_ACCEPT : input.accept;
  const effectiveLanguages = input.languages.length === 0 ? NEUTRAL_LANGUAGE : input.languages;

  const { scored, traces } = score(input, effectiveAccept, effectiveLanguages, fallbackConfig, true);
  const classification = classify(input, traces);
  const selected = scored[0]?.candidate ?? null;
  const vary = resourceVaryDimensions(input.candidates);

  // Counterfactuals against the neutral (absent-header) defaults. Computed
  // on the success/feasible space; used to report per-request influence.
  const neutralMedia = score(input, NEUTRAL_ACCEPT, effectiveLanguages, fallbackConfig, false);
  const neutralLanguage = score(input, effectiveAccept, NEUTRAL_LANGUAGE, fallbackConfig, false);
  const mediaSelectedId = neutralMedia.scored[0]?.candidate.id ?? null;
  const languageSelectedId = neutralLanguage.scored[0]?.candidate.id ?? null;

  const headersPresent = {
    accept: input.accept.length > 0,
    acceptLanguage: input.languages.length > 0,
  };
  const influencedHeaders = {
    // An absent header is the neutral default itself — it cannot "influence".
    accept: headersPresent.accept && (mediaSelectedId !== selected?.id || neutralMedia.scored.length !== scored.length),
    acceptLanguage:
      headersPresent.acceptLanguage &&
      (languageSelectedId !== selected?.id || neutralLanguage.scored.length !== scored.length),
  };

  return {
    ok: classification.failureCategory === 'OK',
    ...classification,
    selected,
    scores: toScores(scored),
    traces,
    warnings: input.warnings,
    vary,
    influencedHeaders,
    counterfactual: {
      withoutAccept: { selectedId: mediaSelectedId, feasibleCount: neutralMedia.scored.length },
      withoutAcceptLanguage: { selectedId: languageSelectedId, feasibleCount: neutralLanguage.scored.length },
    },
  };
}
