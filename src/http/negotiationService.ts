/**
 * Orchestration: joins the contract parsers + kernel for one request.
 *
 * Two responsibilities only:
 *   1. parse both headers under the configured unified policy
 *   2. enforce the request-size guard with a distinct failure category
 *
 * Vary and per-request influence are computed inside the kernel:
 *   - vary: resource-driven cache-key dimensions (correct even for
 *     wildcard / absent headers)
 *   - influencedHeaders: whether THIS request value moved the decision
 */

import type { Candidate } from '../contract/types.js';
import { parseAccept } from '../contract/acceptParser.js';
import { parseAcceptLanguage } from '../contract/languageParser.js';
import type { ParsePolicy } from '../contract/parsePolicy.js';
import { negotiate, type NegotiationResult } from '../kernel/negotiator.js';
import type { LanguageFallbackConfig } from '../kernel/languageMatcher.js';

export interface NegotiationHeaders {
  accept?: string | undefined;
  acceptLanguage?: string | undefined;
}

export interface NegotiationOptions {
  policy: ParsePolicy;
  fallback: LanguageFallbackConfig;
  maxAcceptEntries: number;
}

export interface NegotiationOutcome {
  result: NegotiationResult;
}

export function prepareNegotiation(
  candidates: Candidate[],
  headers: NegotiationHeaders,
  options: NegotiationOptions,
): NegotiationOutcome {
  const accept = parseAccept(headers.accept, options.policy);
  const languages = parseAcceptLanguage(headers.acceptLanguage, options.policy);

  const result = negotiate(
    {
      accept: accept.entries,
      languages: languages.entries,
      candidates,
      warnings: [...accept.warnings, ...languages.warnings],
      acceptRejected: accept.rejected,
      languageRejected: languages.rejected,
    },
    options.fallback,
  );

  // Size guard: absurdly long lists fail with a distinct 400 category.
  const itemCount = (headers.accept?.split(',').length ?? 0) + (headers.acceptLanguage?.split(',').length ?? 0);
  if (itemCount > options.maxAcceptEntries) {
    return {
      result: {
        ...result,
        ok: false,
        httpStatus: 400,
        failureCategory: 'HEADER_TOO_LARGE',
        failureMessage: `Negotiation headers list ${itemCount} items; limit is ${options.maxAcceptEntries}.`,
        selected: null,
        vary: [],
      },
    };
  }

  return { result };
}
