/**
 * Accept header parser (RFC 9110 §12.5.1) — "contract parsing" layer.
 *
 * Responsibilities, and ONLY these:
 *   - tokenize each comma-separated media range
 *   - validate type/subtype tokens and parameters
 *   - parse and strictly validate the q weight (rule: q=0 is LEGAL and
 *     means "explicitly forbidden")
 *   - apply the unified policy to invalid entries / duplicates
 *
 * It never negotiates and never knows about representations.
 */

import type { AcceptEntry, ParseWarning } from './types.js';
import { isToken, parseItem, parseQValue, splitItems } from './headerTokenizer.js';
import type { ParsePolicy } from './parsePolicy.js';
import { DEFAULT_POLICY, WarningCode } from './parsePolicy.js';

export interface ParsedAccept {
  entries: AcceptEntry[];
  warnings: ParseWarning[];
  /** True when policy rejected the whole header. Caller must fail closed. */
  rejected: boolean;
}

function keyOf(type: string, subtype: string, params: Record<string, string>): string {
  const paramKey = Object.keys(params)
    .sort()
    .map((name) => `${name}=${params[name]}`)
    .join('&');
  return `${type}/${subtype}|${paramKey}`;
}

export function parseAccept(header: string | undefined, policy: ParsePolicy = DEFAULT_POLICY): ParsedAccept {
  const warnings: ParseWarning[] = [];
  const entries: AcceptEntry[] = [];
  if (header === undefined || header.trim() === '') {
    return { entries, warnings, rejected: false };
  }

  const seen = new Map<string, number>();
  const items = splitItems(header);
  let position = 0;

  for (const raw of items) {
    position++;
    const item = parseItem(raw);
    const fail = (code: string, message: string): boolean => {
      if (policy.invalidEntryPolicy === 'ignore') return false;
      warnings.push({ code, message, item: raw });
      return policy.invalidEntryPolicy === 'reject-header';
    };

    if (item.main === '') {
      if (fail(WarningCode.MALFORMED_ENTRY, 'empty media range')) return { entries: [], warnings, rejected: true };
      continue;
    }

    const slash = item.main.indexOf('/');
    if (slash === -1) {
      if (fail(WarningCode.MALFORMED_ENTRY, `media range "${item.main}" is missing "/"`)) {
        return { entries: [], warnings, rejected: true };
      }
      continue;
    }
    const type = item.main.slice(0, slash).toLowerCase();
    const subtype = item.main.slice(slash + 1).toLowerCase();
    const typeOk = type === '*' ? subtype === '*' : isToken(type);
    const subtypeOk = isToken(subtype) || subtype === '*';
    if (!typeOk || !subtypeOk || (type === '*' && subtype !== '*')) {
      if (fail(WarningCode.MALFORMED_ENTRY, `malformed media range "${item.main}"`)) {
        return { entries: [], warnings, rejected: true };
      }
      continue;
    }

    let q = 1;
    const params: Record<string, string> = {};
    const paramNames = new Set<string>();
    let malformed = false;
    for (const param of item.params) {
      const name = param.name.toLowerCase();
      // RFC 9110 §5.6.6: a sender MUST NOT generate duplicate parameters;
      // a duplicate (including a second q) invalidates the whole item.
      if (paramNames.has(name)) {
        if (fail(WarningCode.MALFORMED_PARAMETER, `duplicate parameter "${name}" in "${raw}"`)) {
          return { entries: [], warnings, rejected: true };
        }
        malformed = true;
        break;
      }
      paramNames.add(name);
      if (name === 'q') {
        const parsed = parseQValue(param.value);
        if (parsed === null) {
          if (fail(WarningCode.INVALID_Q, `invalid q value "${param.value}" (allowed 0–1, max 3 decimals)`)) {
            return { entries: [], warnings, rejected: true };
          }
          malformed = true;
          break;
        }
        q = parsed;
        continue;
      }
      if (name === '' || !isToken(name) || param.value === '') {
        if (fail(WarningCode.MALFORMED_PARAMETER, `malformed parameter "${param.raw}"`)) {
          return { entries: [], warnings, rejected: true };
        }
        malformed = true;
        break;
      }
      params[name] = param.value.toLowerCase();
    }
    if (malformed) continue;

    const entry: AcceptEntry = { raw, type, subtype, params, q, position };
    const key = keyOf(type, subtype, params);
    const prior = seen.get(key);
    if (prior !== undefined) {
      warnings.push({ code: WarningCode.DUPLICATE_ENTRY, message: `duplicate range "${raw}"`, item: raw });
      if (policy.duplicatePolicy === 'first-wins') continue;
      entries[prior] = entry;
      continue;
    }
    seen.set(key, entries.length);
    entries.push(entry);
  }

  return { entries, warnings, rejected: false };
}
