/**
 * Unified error taxonomy for parsing and negotiation failures.
 *
 * Parsing failures are client faults (HTTP 400). Negotiation failures are
 * "not acceptable" (HTTP 406). Every failure carries a stable machine
 * readable {@link NegotiationErrorCode} so tests can assert the exact
 * failure category rather than a status number alone.
 */
export type NegotiationErrorCode =
  | 'MALFORMED_HEADER'
  | 'INVALID_WEIGHT'
  | 'DUPLICATE_PARAMETER'
  | 'UNKNOWN_PARAMETER'
  | 'UNACCEPTABLE_MEDIA_TYPE'
  | 'UNACCEPTABLE_LANGUAGE'
  | 'NO_VARIANT_FOR_COMBINATION';

export type FailureStage = 'parse-accept' | 'parse-language' | 'negotiate-media' | 'negotiate-language' | 'combine';

export class NegotiationError extends Error {
  readonly code: NegotiationErrorCode;
  readonly stage: FailureStage;
  readonly headerName: string | null;
  readonly detail: Record<string, unknown>;

  constructor(
    code: NegotiationErrorCode,
    stage: FailureStage,
    message: string,
    options: { headerName?: string; detail?: Record<string, unknown> } = {},
  ) {
    super(message);
    this.name = 'NegotiationError';
    this.code = code;
    this.stage = stage;
    this.headerName = options.headerName ?? null;
    this.detail = options.detail ?? {};
  }
}

/** Non-fatal observations recorded verbatim in the negotiation trace. */
export type NoticeCode = 'DUPLICATE_RANGE' | 'IGNORED_EXTENSION_PARAMETER' | 'IGNORED_UNKNOWN_PARAMETER';

export interface Notice {
  readonly code: NoticeCode;
  readonly headerName: string;
  readonly rangeIndex: number;
  readonly message: string;
}
