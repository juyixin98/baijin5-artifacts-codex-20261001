/**
 * Unified policy for malformed input, declared independently of the
 * parsers so Accept and Accept-Language apply IDENTICAL strategies.
 *
 *   invalidEntryPolicy:
 *     - 'drop-with-warning': the offending item is removed, parsing
 *       continues, and a structured warning is recorded. (default)
 *     - 'reject-header':     the whole header is rejected; the negotiator
 *                            then treats the resource as not acceptable.
 *     - 'ignore':            the offending item is removed silently.
 *
 *   duplicatePolicy:
 *     - 'first-wins': keep the first occurrence, warn on later ones.
 *     - 'last-wins':  replace the earlier occurrence, warn.
 *
 * Unknown parameters: Accept defines all non-q parameters as Accept-Params
 * that participate in matching (RFC 9110 §12.5.1), so they are never
 * "unknown" there. Accept-Language defines only q; any other parameter is
 * unknown and handled with the same invalidEntryPolicy.
 */

export type InvalidEntryPolicy = 'drop-with-warning' | 'reject-header' | 'ignore';
export type DuplicatePolicy = 'first-wins' | 'last-wins';

export interface ParsePolicy {
  invalidEntryPolicy: InvalidEntryPolicy;
  duplicatePolicy: DuplicatePolicy;
}

export const DEFAULT_POLICY: ParsePolicy = {
  invalidEntryPolicy: 'drop-with-warning',
  duplicatePolicy: 'first-wins',
};

/** Warning codes shared across parsers (the "unified" vocabulary). */
export const WarningCode = {
  INVALID_Q: 'INVALID_Q',
  MALFORMED_ENTRY: 'MALFORMED_ENTRY',
  MALFORMED_PARAMETER: 'MALFORMED_PARAMETER',
  UNKNOWN_PARAMETER: 'UNKNOWN_PARAMETER',
  DUPLICATE_ENTRY: 'DUPLICATE_ENTRY',
  EMPTY_HEADER: 'EMPTY_HEADER',
} as const;

export type WarningCode = (typeof WarningCode)[keyof typeof WarningCode];
