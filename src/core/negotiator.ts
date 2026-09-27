/**
 * Negotiation execution core.
 *
 * Pure, deterministic, side-effect free: given a resource's variants and the
 * two parsed header structures it returns either a winner or a classified
 * failure, plus an exhaustive {@link NegotiationTrace} explaining every step.
 *
 * Selection model
 * ---------------
 * 1. Every variant is scored independently on the media axis and the
 *    language axis. A variant's quality for an axis is taken from the MOST
 *    SPECIFIC matching header range (exact > type/* > *​/*; longest language
 *    prefix > shorter > "*"). This is what gives wildcard ranges lower
 *    priority than concrete ones.
 * 2. q=0 on a matching range is an explicit prohibition (never "missing").
 * 3. combined = mediaQuality * languageQuality; the highest combined wins.
 * 4. Equal combined is broken by: media specificity desc, language
 *    specificity desc, server declaration order asc, representation id asc.
 *    Steps 3-4 make same-weight ties stable across runs and processes.
 * 5. Media and language are negotiated separately: if NO range matches an
 *    axis (or every match is q=0) the failure names that axis.
 */
import { randomUUID } from 'node:crypto';
import { NegotiationError } from '../contract/errors.js';
import { parseAcceptHeader, type ParsedAcceptRange } from '../contract/accept-parser.js';
import { parseAcceptLanguageHeader, type ParsedLanguageRange } from '../contract/language-parser.js';
import type {
  CandidateScore,
  MediaType,
  NegotiationTrace,
  Representation,
  Resource,
} from './types.js';
import { serviceVersion } from './version.js';

export type AbsentHeaderPolicy = 'wildcard' | 'default';
export type UnknownParameterPolicy = 'ignore' | 'reject';
/**
 * Language fallback policy:
 *  - "lookup"    : RFC 4647-style truncation is allowed (request en-gb can be
 *                  served an ancestor en); useful for real browsers/clients.
 *  - "filtering" : strict RFC 9110 basic filtering, en-gb matches only
 *                  en-gb / en-gb-*; no ancestor fallback.
 */
export type LanguageFallbackPolicy = 'lookup' | 'filtering';

export interface NegotiationConfig {
  readonly absentAccept: AbsentHeaderPolicy;
  readonly absentAcceptLanguage: AbsentHeaderPolicy;
  readonly unknownParameters: UnknownParameterPolicy;
  readonly languageFallback: LanguageFallbackPolicy;
  readonly defaultMediaType: string;
  readonly defaultLanguage: string;
}

export const DEFAULT_CONFIG: NegotiationConfig = {
  absentAccept: 'wildcard',
  absentAcceptLanguage: 'wildcard',
  unknownParameters: 'ignore',
  languageFallback: 'lookup',
  defaultMediaType: 'application/json',
  defaultLanguage: 'en',
};

interface MediaMatch {
  readonly quality: number;
  readonly specificity: number; // 2 = exact, 1 = type/*, 0 = */*
  readonly range: ParsedAcceptRange;
}

interface LanguageMatch {
  readonly quality: number;
  /**
   * Specificity tier:
   *   3 exact tag equality
   *   2 range is a strict prefix of the served tag (filtering, e.g. en -> en-gb)
   *   1 RFC 4647 lookup truncation fallback (range longer, served tag is its
   *     prefix, e.g. requested en-gb but only en is served)
   *   0 wildcard
   * Tiers keep exact/prefix matches ahead of truncation fallbacks.
   */
  readonly specificity: number;
  readonly fallback: boolean;
  readonly range: ParsedLanguageRange;
}

const SPECIFICITY_EXACT = 2;
const SPECIFICITY_TYPE_WILDCARD = 1;
const SPECIFICITY_FULL_WILDCARD = 0;

const LANG_EXACT = 3;
const LANG_PREFIX = 2;
const LANG_TRUNCATION_FALLBACK = 1;
const LANG_WILDCARD = 0;

function parametersSatisfy(range: ParsedAcceptRange, mediaType: MediaType): boolean {
  for (const [name, expected] of range.constraints) {
    if (mediaType.parameters.get(name) !== expected) return false;
  }
  return true;
}

function matchMedia(range: ParsedAcceptRange, mediaType: MediaType): boolean {
  if (range.wildcardType) return parametersSatisfy(range, mediaType);
  if (range.type !== mediaType.type) return false;
  if (range.wildcardSubtype) return parametersSatisfy(range, mediaType);
  return range.subtype === mediaType.subtype && parametersSatisfy(range, mediaType);
}

/** Quality + most-specific matching range for one media type, or null when no range matches. */
function scoreMedia(
  mediaType: MediaType,
  ranges: readonly ParsedAcceptRange[],
): MediaMatch | null {
  let best: MediaMatch | null = null;
  for (const range of ranges) {
    if (!matchMedia(range, mediaType)) continue;
    const specificity = range.wildcardType
      ? SPECIFICITY_FULL_WILDCARD
      : range.wildcardSubtype
        ? SPECIFICITY_TYPE_WILDCARD
        : SPECIFICITY_EXACT;
    // Most specific wins; header order breaks equal specificity (first wins).
    if (best === null || specificity > best.specificity) {
      best = { quality: range.quality, specificity, range };
    }
  }
  return best;
}

function languageRank(
  tag: string,
  range: ParsedLanguageRange,
  fallbackPolicy: LanguageFallbackPolicy,
): { tier: number; sub: number; fallback: boolean } | null {
  if (range.wildcard) return { tier: LANG_WILDCARD, sub: 0, fallback: false };
  if (range.tag === null) return null;

  if (range.tag === tag) {
    return { tier: LANG_EXACT, sub: range.specificity, fallback: false };
  }
  if (tag.startsWith(`${range.tag}-`)) {
    // Served tag is more specific than the range (basic filtering prefix).
    return { tier: LANG_PREFIX, sub: range.specificity, fallback: false };
  }
  if (fallbackPolicy === 'lookup' && range.tag.startsWith(`${tag}-`)) {
    // Requested tag is more specific; served tag is a truncation ancestor.
    // Closer ancestor (shorter requested range) ranks higher.
    return { tier: LANG_TRUNCATION_FALLBACK, sub: -range.specificity, fallback: true };
  }
  return null;
}

const RANK_SCALE = 1000;

function scoreLanguage(
  tag: string,
  ranges: readonly ParsedLanguageRange[],
  fallbackPolicy: LanguageFallbackPolicy,
): LanguageMatch | null {
  let best: { match: LanguageMatch; rank: number } | null = null;
  for (const range of ranges) {
    const ranked = languageRank(tag, range, fallbackPolicy);
    if (ranked === null) continue;
    const rank = ranked.tier * RANK_SCALE + ranked.sub;
    // Strictly-greater keeps the first header range on equal rank (stable).
    if (best === null || rank > best.rank) {
      best = { match: { quality: range.quality, specificity: ranked.tier, fallback: ranked.fallback, range }, rank };
    }
  }
  return best === null ? null : best.match;
}

function describeMediaRange(range: ParsedAcceptRange): string {
  const shape = range.wildcardType
    ? '*/*'
    : `${range.type}/${range.wildcardSubtype ? '*' : range.subtype}`;
  const params = [...range.constraints.entries()].map(([k, v]) => `;${k}=${v}`).join('');
  return `${shape}${params};q=${range.quality}`;
}

function describeLanguageRange(range: ParsedLanguageRange): string {
  return `${range.wildcard ? '*' : range.tag};q=${range.quality}`;
}

function serializeMediaRanges(ranges: readonly ParsedAcceptRange[]): ReadonlyArray<Record<string, unknown>> {
  return ranges.map((r) => ({
    index: r.index,
    range: r.wildcardType ? '*/*' : `${r.type}/${r.wildcardSubtype ? '*' : r.subtype}`,
    constraints: Object.fromEntries(r.constraints),
    q: r.quality,
  }));
}

function serializeLanguageRanges(ranges: readonly ParsedLanguageRange[]): ReadonlyArray<Record<string, unknown>> {
  return ranges.map((r) => ({ index: r.index, range: r.wildcard ? '*' : r.tag, q: r.quality }));
}

export interface NegotiateInput {
  readonly resource: Resource;
  readonly acceptHeader: string | null;
  readonly acceptLanguageHeader: string | null;
  readonly config?: Partial<NegotiationConfig> | undefined;
}

interface PreparedHeaders {
  readonly acceptRanges: readonly ParsedAcceptRange[];
  readonly languageRanges: readonly ParsedLanguageRange[];
  readonly notices: import('../contract/errors.js').Notice[];
  readonly acceptSynthetic: boolean;
  readonly languageSynthetic: boolean;
}

function prepareHeaders(input: NegotiateInput, cfg: NegotiationConfig): PreparedHeaders {
  const notices: import('../contract/errors.js').Notice[] = [];

  let acceptRanges: readonly ParsedAcceptRange[];
  let acceptSynthetic = false;
  if (input.acceptHeader === null || input.acceptHeader.trim() === '') {
    if (cfg.absentAccept === 'default') {
      const parsed = parseAcceptHeader(cfg.defaultMediaType, cfg.unknownParameters);
      acceptRanges = parsed.ranges;
      notices.push(...parsed.notices);
    } else {
      acceptRanges = [
        { index: -1, raw: '*/* (synthetic: header absent)', type: '*', subtype: '*', wildcardType: true, wildcardSubtype: true, constraints: new Map(), quality: 1 },
      ];
    }
    acceptSynthetic = true;
  } else {
    const parsed = parseAcceptHeader(input.acceptHeader, cfg.unknownParameters);
    acceptRanges = parsed.ranges;
    notices.push(...parsed.notices);
  }

  let languageRanges: readonly ParsedLanguageRange[];
  let languageSynthetic = false;
  if (input.acceptLanguageHeader === null || input.acceptLanguageHeader.trim() === '') {
    if (cfg.absentAcceptLanguage === 'default') {
      const parsed = parseAcceptLanguageHeader(cfg.defaultLanguage);
      languageRanges = parsed.ranges;
      notices.push(...parsed.notices);
    } else {
      languageRanges = [
        { index: -1, raw: '* (synthetic: header absent)', tag: null, wildcard: true, quality: 1, specificity: 0 },
      ];
    }
    languageSynthetic = true;
  } else {
    const parsed = parseAcceptLanguageHeader(input.acceptLanguageHeader);
    languageRanges = parsed.ranges;
    notices.push(...parsed.notices);
  }

  return { acceptRanges, languageRanges, notices, acceptSynthetic, languageSynthetic };
}

/**
 * Which request headers could actually change the chosen variant? A header
 * only matters when the resource offers variation on that axis; otherwise
 * sending it in Vary would claim a dependency that does not exist.
 */
function computeVary(resource: Resource, acceptSynthetic: boolean, languageSynthetic: boolean): string[] {
  const vary: string[] = [];
  const distinctMedia = new Set(resource.representations.map((r) => `${r.mediaType.type}/${r.mediaType.subtype}`)).size > 1;
  const distinctParams = new Set(
    resource.representations.map((r) => JSON.stringify([...r.mediaType.parameters.entries()].sort())),
  ).size > 1;
  const distinctLanguages = new Set(resource.representations.map((r) => r.language)).size > 1;
  // A present header is always a potential cache discriminator; a synthetic
  // wildcard only matters when the resource actually varies on the axis.
  if (!acceptSynthetic || distinctMedia || distinctParams) vary.push('Accept');
  if (!languageSynthetic || distinctLanguages) vary.push('Accept-Language');
  return vary;
}

export function negotiate(input: NegotiateInput): NegotiationTrace {
  const start = process.hrtime.bigint();
  const cfg: NegotiationConfig = { ...DEFAULT_CONFIG, ...input.config };
  const runId = randomUUID();
  const startedAt = new Date().toISOString();

  let prepared: PreparedHeaders;
  try {
    prepared = prepareHeaders(input, cfg);
  } catch (err) {
    const elapsedMs = Number(process.hrtime.bigint() - start) / 1e6;
    if (err instanceof NegotiationError) {
      // The malformed header itself determined the (failed) response, so the
      // cache discriminator is that header even though no variant was chosen.
      const parseVary = err.headerName === 'Accept-Language' ? ['Accept-Language'] : err.headerName === 'Accept' ? ['Accept'] : [];
      const parseSteps = [`verdict: FAILURE ${err.code} @ ${err.stage} - ${err.message}`];
      return buildFailureTrace(input, runId, startedAt, [], [], [], err, [], elapsedMs, parseVary, parseSteps);
    }
    throw err;
  }

  const { acceptRanges, languageRanges, notices, acceptSynthetic, languageSynthetic } = prepared;
  const vary = computeVary(input.resource, acceptSynthetic, languageSynthetic);

  const steps: string[] = [];
  steps.push(
    `parse: Accept "${input.acceptHeader ?? '(absent)'}" -> ${acceptRanges.length} range(s), most-specific-match wins; q=0 forbids`,
  );
  steps.push(
    `parse: Accept-Language "${input.acceptLanguageHeader ?? '(absent)'}" -> ${languageRanges.length} range(s); longest-prefix-match wins; q=0 forbids`,
  );
  steps.push(`score: evaluating ${input.resource.representations.length} candidate representation(s), combined = mediaQ * languageQ`);

  const scores: CandidateScore[] = input.resource.representations.map((rep, order) => {
    const media = scoreMedia(rep.mediaType, acceptRanges);
    const language = scoreLanguage(rep.language, languageRanges, cfg.languageFallback);

    let reason: string;
    let mediaQuality: number | null;
    let languageQuality: number | null;

    if (media === null) {
      mediaQuality = null;
      reason = `media type ${rep.mediaType.type}/${rep.mediaType.subtype} matches no Accept range`;
    } else {
      mediaQuality = media.quality;
      reason = `media matched ${describeMediaRange(media.range)}`;
    }
    if (language === null) {
      languageQuality = null;
      reason += `; language ${rep.language} matches no Accept-Language range`;
    } else {
      languageQuality = language.quality;
      reason += `; language matched ${describeLanguageRange(language.range)}${language.fallback ? ' via truncation fallback' : ''}`;
    }

    const mediaZero = mediaQuality === 0;
    const langZero = languageQuality === 0;
    if (mediaZero) reason += '; EXPLICITLY FORBIDDEN by media q=0';
    if (langZero) reason += '; EXPLICITLY FORBIDDEN by language q=0';

    let combined: number | null = null;
    if (mediaQuality !== null && languageQuality !== null && !mediaZero && !langZero) {
      combined = mediaQuality * languageQuality;
    } else if (mediaQuality !== null && languageQuality !== null) {
      combined = 0; // explicitly prohibited on at least one axis
    }

    steps.push(
      `score: [${rep.id}] ${rep.mediaType.type}/${rep.mediaType.subtype}+${rep.language} -> mediaQ=${mediaQuality ?? 'NO-MATCH'} langQ=${languageQuality ?? 'NO-MATCH'} combined=${combined ?? 'unselectable'} (${reason})`,
    );

    return {
      representationId: rep.id,
      mediaType: `${rep.mediaType.type}/${rep.mediaType.subtype}`,
      language: rep.language,
      mediaQuality,
      languageQuality,
      combined,
      order,
      reason,
    };
  });

  // Classify failure on each axis independently.
  const mediaMatched = scores.filter((s) => s.mediaQuality !== null);
  const languageMatched = scores.filter((s) => s.languageQuality !== null);
  const mediaAllowed = scores.filter((s) => (s.mediaQuality ?? 0) > 0);
  const languageAllowed = scores.filter((s) => (s.languageQuality ?? 0) > 0);
  const selectable = scores.filter((s) => s.combined !== null && s.combined > 0);

  let failure: NegotiationError | null = null;
  if (mediaMatched.length === 0) {
    failure = new NegotiationError(
      'UNACCEPTABLE_MEDIA_TYPE',
      'negotiate-media',
      `None of the ${scores.length} representation(s) matches any offered Accept range`,
      { detail: { offered: [...new Set(scores.map((s) => s.mediaType))] } },
    );
  } else if (mediaAllowed.length === 0) {
    failure = new NegotiationError(
      'UNACCEPTABLE_MEDIA_TYPE',
      'negotiate-media',
      'Every matching media type carries an explicit q=0 prohibition',
      { detail: { prohibited: [...new Set(mediaMatched.map((s) => s.mediaType))] } },
    );
  } else if (languageMatched.length === 0) {
    failure = new NegotiationError(
      'UNACCEPTABLE_LANGUAGE',
      'negotiate-language',
      `None of the representation languages matches any Accept-Language range`,
      { detail: { offered: [...new Set(scores.map((s) => s.language))] } },
    );
  } else if (languageAllowed.length === 0) {
    failure = new NegotiationError(
      'UNACCEPTABLE_LANGUAGE',
      'negotiate-language',
      'Every matching language carries an explicit q=0 prohibition',
      { detail: { prohibited: [...new Set(languageMatched.map((s) => s.language))] } },
    );
  } else if (selectable.length === 0) {
    // Both axes match and allow something, but never on the same variant.
    failure = new NegotiationError(
      'NO_VARIANT_FOR_COMBINATION',
      'combine',
      'Media and language are individually acceptable but no single representation satisfies both',
      { detail: { mediaAllowed: mediaAllowed.map((s) => s.representationId), languageAllowed: languageAllowed.map((s) => s.representationId) } },
    );
  }

  const elapsedMs = Number(process.hrtime.bigint() - start) / 1e6;

  if (failure !== null) {
    steps.push(`verdict: FAILURE ${failure.code} @ ${failure.stage} - ${failure.message}`);
    return buildFailureTrace(
      input,
      runId,
      startedAt,
      acceptRanges,
      languageRanges,
      scores,
      failure,
      notices,
      elapsedMs,
      vary,
      steps,
    );
  }

  // Deterministic winner selection with stable tie-break keys.
  const repById = new Map(input.resource.representations.map((r) => [r.id, r]));
  const specificityOf = (score: CandidateScore): { media: number; language: number } => {
    const rep = repById.get(score.representationId)!;
    const mediaMatch = scoreMedia(rep.mediaType, acceptRanges)!;
    const langMatch = scoreLanguage(rep.language, languageRanges, cfg.languageFallback)!;
    return { media: mediaMatch.specificity, language: langMatch.specificity };
  };

  const winnerScore = [...selectable].sort((a, b) => {
    if (b.combined! !== a.combined!) return b.combined! - a.combined!;
    const sa = specificityOf(a);
    const sb = specificityOf(b);
    if (sb.media !== sa.media) return sb.media - sa.media;
    if (sb.language !== sa.language) return sb.language - sa.language;
    if (a.order !== b.order) return a.order - b.order;
    return a.representationId < b.representationId ? -1 : a.representationId > b.representationId ? 1 : 0;
  })[0]!;

  const winnerRep = repById.get(winnerScore.representationId)!;
  const winnerSpec = specificityOf(winnerScore);
  steps.push(
    `verdict: WINNER [${winnerRep.id}] ${winnerScore.mediaType}+${winnerScore.language} combined=${winnerScore.combined!.toFixed(3)} ` +
      `(mediaSpec=${winnerSpec.media}, langSpec=${winnerSpec.language}, order=${winnerScore.order}); Vary=${vary.join(',') || '(none)'}`,
  );
  return {
    runId,
    startedAt,
    serviceVersion: serviceVersion(),
    resourceId: input.resource.id,
    acceptHeaderRaw: input.acceptHeader,
    acceptLanguageHeaderRaw: input.acceptLanguageHeader,
    mediaRanges: serializeMediaRanges(acceptRanges),
    languageRanges: serializeLanguageRanges(languageRanges),
    steps,
    candidateScores: scores,
    winner: {
      representationId: winnerRep.id,
      mediaType: `${winnerRep.mediaType.type}/${winnerRep.mediaType.subtype}`,
      language: winnerRep.language,
      quality: winnerScore.combined!,
    },
    failure: null,
    notices: notices.map((n) => ({ ...n })),
    vary,
    elapsedMs,
  };
}

function buildFailureTrace(
  input: NegotiateInput,
  runId: string,
  startedAt: string,
  acceptRanges: readonly ParsedAcceptRange[],
  languageRanges: readonly ParsedLanguageRange[],
  scores: readonly CandidateScore[],
  error: NegotiationError,
  notices: import('../contract/errors.js').Notice[],
  elapsedMs: number,
  vary: string[] = [],
  steps: string[] = [],
): NegotiationTrace {
  return {
    runId,
    startedAt,
    serviceVersion: serviceVersion(),
    resourceId: input.resource.id,
    acceptHeaderRaw: input.acceptHeader,
    acceptLanguageHeaderRaw: input.acceptLanguageHeader,
    mediaRanges: serializeMediaRanges(acceptRanges),
    languageRanges: serializeLanguageRanges(languageRanges),
    steps,
    candidateScores: scores,
    winner: null,
    failure: { code: error.code, stage: error.stage, message: error.message },
    notices: notices.map((n) => ({ ...n })),
    vary,
    elapsedMs,
  };
}

export function isFailureTrace(trace: NegotiationTrace): trace is NegotiationTrace & { failure: NonNullable<NegotiationTrace['failure']> } {
  return trace.failure !== null;
}

export { NegotiationError };
