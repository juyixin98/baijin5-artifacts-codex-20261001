/**
 * Strict quality-value parser.
 *
 * Accepted shapes: "0", "0." followed by 1..3 digits, "1", or "1." followed
 * by 1..3 zeroes. (RFC 9110 technically permits a trailing dot with zero
 * fraction digits; this parser deliberately requires at least one digit so
 * weights like "1." and "0." are rejected as malformed.)
 *
 * Anything else (1.5, 0.1234, -0.1, "1.", "+1", "0x1", whitespace inside)
 * is rejected with INVALID_WEIGHT. A parsed 0 is a perfectly legal value:
 * callers treat it as an explicit prohibition, never as "missing".
 */
import { NegotiationError } from './errors.js';

const QVALUE = /^(?:0(?:\.\d{1,3})?|1(?:\.0{1,3})?)$/;

export function parseQValue(raw: string, stage: FailureStageLike, headerName: string): number {
  if (!QVALUE.test(raw)) {
    throw new NegotiationError('INVALID_WEIGHT', stage, `Invalid quality value "${raw}" (expected 0..1 with at most 3 decimals)`, {
      headerName,
      detail: { rawWeight: raw },
    });
  }
  const value = Number(raw);
  if (!Number.isFinite(value) || value < 0 || value > 1) {
    throw new NegotiationError('INVALID_WEIGHT', stage, `Quality value "${raw}" out of range`, {
      headerName,
      detail: { rawWeight: raw },
    });
  }
  return value;
}

type FailureStageLike = import('./errors.js').FailureStage;
