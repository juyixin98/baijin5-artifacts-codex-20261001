/**
 * HTTP presentation helpers: turn a chosen representation into concrete
 * response metadata. Kept separate from routing so the mapping is unit
 * testable without a server.
 */
import type { NegotiationTrace, Representation } from '../core/types.js';
import { NegotiationError, type NegotiationErrorCode } from '../contract/errors.js';

export function responseContentType(rep: Representation): string {
  const params = [...rep.mediaType.parameters.entries()].map(([k, v]) => `; ${k}=${v}`).join('');
  return `${rep.mediaType.type}/${rep.mediaType.subtype}${params}; charset=${rep.charset}`;
}

export interface ErrorView {
  readonly status: number;
  readonly body: {
    readonly error: NegotiationErrorCode | 'INTERNAL_ERROR' | 'NOT_FOUND';
    readonly status: number;
    readonly stage?: string;
    readonly message: string;
    readonly runId?: string;
    readonly detail?: Record<string, unknown>;
  };
}

/** Map classified negotiation failures to HTTP status codes (400 / 406). */
export function negotiationErrorView(error: NegotiationError, runId?: string): ErrorView {
  const parseCodes: ReadonlySet<NegotiationErrorCode> = new Set([
    'MALFORMED_HEADER',
    'INVALID_WEIGHT',
    'DUPLICATE_PARAMETER',
    'UNKNOWN_PARAMETER',
  ]);
  const status = parseCodes.has(error.code) ? 400 : 406;
  return {
    status,
    body: {
      error: error.code,
      status,
      stage: error.stage,
      message: error.message,
      ...(runId ? { runId } : {}),
      ...(Object.keys(error.detail).length ? { detail: error.detail } : {}),
    },
  };
}

export function varyHeader(trace: NegotiationTrace): string {
  return trace.vary.join(', ');
}
