/**
 * Shared contract types for header parsing and negotiation.
 *
 * These types are the boundary between the "contract parsing" module
 * (src/contract/*) and the "execution kernel" (src/kernel/*).
 */

/** A structured, non-fatal problem found while parsing a header. */
export interface ParseWarning {
  /** Stable machine-readable code, e.g. INVALID_Q, DUPLICATE_ENTRY. */
  code: string;
  /** Human-readable explanation, safe to expose to clients. */
  message: string;
  /** The raw header item that caused the warning, when applicable. */
  item?: string;
}

/** One valid media range parsed from an Accept header. */
export interface AcceptEntry {
  /** Raw header item, trimmed. */
  raw: string;
  /** Lowercased type, or '*'. */
  type: string;
  /** Lowercased subtype, or '*'. */
  subtype: string;
  /**
   * Accept-parameters (every parameter except q). Keys are lowercased.
   * Policy: these participate in matching — a candidate must carry the
   * same parameter name/value (compared case-insensitively) to match.
   */
  params: Record<string, string>;
  /** Quality weight in [0, 1]. */
  q: number;
  /** 1-based position of this item in the original header (stable tie-break input). */
  position: number;
}

/** One valid language range parsed from an Accept-Language header. */
export interface LanguageRangeEntry {
  /** Raw header item, trimmed. */
  raw: string;
  /** Lowercased range, e.g. 'zh-cn', or '*'. */
  range: string;
  /** Subtags of the range (empty for '*'). */
  subtags: string[];
  /** Quality weight in [0, 1]. */
  q: number;
  /** 1-based position of this item in the original header. */
  position: number;
}

/** A representation candidate as seen by the negotiation kernel. */
export interface Candidate {
  /** Stable identifier of the representation (unique within a resource). */
  id: string;
  /** Lowercased media type, e.g. 'text'. */
  type: string;
  /** Lowercased media subtype, e.g. 'html'. */
  subtype: string;
  /** Media type parameters of the representation (lowercased keys). */
  params: Record<string, string>;
  /** Lowercased BCP-47 language tag, e.g. 'zh-cn'. */
  language: string;
  /** Declaration order (0-based); the final deterministic tie-break. */
  ordinal: number;
}
