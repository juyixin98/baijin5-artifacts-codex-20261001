/**
 * Domain model shared by the negotiation core, the state adapter and the
 * HTTP layer. A resource has several {@link Representation variants}; each
 * variant is one (mediaType, language) pair with a concrete body.
 */

/** A media type understood by the server, e.g. "application/json". */
export interface MediaType {
  readonly type: string;
  readonly subtype: string;
  /** Lower-cased parameter name -> value, e.g. version -> "2". */
  readonly parameters: ReadonlyMap<string, string>;
}

/** A concrete representation the server can actually return. */
export interface Representation {
  readonly id: string;
  readonly mediaType: MediaType;
  /** BCP 47 language tag, canonical lower-case subtags, e.g. "en-gb". */
  readonly language: string;
  readonly charset: string;
  readonly body: string;
}

export interface Resource {
  readonly id: string;
  readonly name: string;
  readonly representations: readonly Representation[];
}

/** Result of scoring a single header range against a single variant. */
export interface CandidateScore {
  readonly representationId: string;
  readonly mediaType: string;
  readonly language: string;
  /** Accept range quality (0..1), or null when the range forbids it. */
  readonly mediaQuality: number | null;
  /** Accept-Language quality (0..1), or null when forbidden. */
  readonly languageQuality: number | null;
  /** Combined quality = mediaQuality * languageQuality; null if either forbids. */
  readonly combined: number | null;
  /** Stable tie-break keys; smaller wins. */
  readonly order: number;
  readonly reason: string;
}

export interface NegotiationTrace {
  readonly runId: string;
  readonly startedAt: string;
  readonly serviceVersion: string;
  readonly resourceId: string;
  readonly acceptHeaderRaw: string | null;
  readonly acceptLanguageHeaderRaw: string | null;
  readonly mediaRanges: ReadonlyArray<Record<string, unknown>>;
  readonly languageRanges: ReadonlyArray<Record<string, unknown>>;
  /** Ordered human-readable computation log: parse -> per-candidate scoring -> verdict. */
  readonly steps: readonly string[];
  readonly candidateScores: readonly CandidateScore[];
  readonly winner: { representationId: string; mediaType: string; language: string; quality: number } | null;
  readonly failure: { code: string; stage: string; message: string } | null;
  readonly notices: ReadonlyArray<Record<string, unknown>>;
  /** Headers the response MUST vary on, given the headers that actually mattered. */
  readonly vary: readonly string[];
  readonly elapsedMs: number;
}
