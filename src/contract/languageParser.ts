/**
 * Accept-Language header parser (RFC 9110 §12.5.4) — "contract parsing".
 * Same strict-q and unified-policy behavior as the Accept parser, but
 * language ranges have a different grammar: 1*8ALPHA *( "-" 1*8alphanum )
 * plus the single "*" wildcard. Any parameter other than q is UNKNOWN.
 */

import type { LanguageRangeEntry, ParseWarning } from './types.js';
import { parseItem, parseQValue, splitItems } from './headerTokenizer.js';
import type { ParsePolicy } from './parsePolicy.js';
import { DEFAULT_POLICY, WarningCode } from './parsePolicy.js';

export interface ParsedLanguages {
  entries: LanguageRangeEntry[];
  warnings: ParseWarning[];
  rejected: boolean;
}

const LANGUAGE_TAG = /^[a-z]{1,8}(?:-[a-z0-9]{1,8})*$/i;

export function parseAcceptLanguage(
  header: string | undefined,
  policy: ParsePolicy = DEFAULT_POLICY,
): ParsedLanguages {
  const warnings: ParseWarning[] = [];
  const entries: LanguageRangeEntry[] = [];
  if (header === undefined || header.trim() === '') {
    return { entries, warnings, rejected: false };
  }

  const seen = new Set<string>();
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
      if (fail(WarningCode.MALFORMED_ENTRY, 'empty language range')) return { entries: [], warnings, rejected: true };
      continue;
    }
    const range = item.main.toLowerCase();
    if (range !== '*' && !LANGUAGE_TAG.test(range)) {
      if (fail(WarningCode.MALFORMED_ENTRY, `malformed language range "${item.main}"`)) {
        return { entries: [], warnings, rejected: true };
      }
      continue;
    }

    let q = 1;
    let malformed = false;
    const paramNames = new Set<string>();
    for (const param of item.params) {
      const name = param.name.toLowerCase();
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
      // Accept-Language defines no parameters besides q.
      if (fail(WarningCode.UNKNOWN_PARAMETER, `unknown parameter "${param.raw}" for language range`)) {
        return { entries: [], warnings, rejected: true };
      }
      malformed = true;
      break;
    }
    if (malformed) continue;

    if (seen.has(range)) {
      warnings.push({ code: WarningCode.DUPLICATE_ENTRY, message: `duplicate language range "${raw}"`, item: raw });
      if (policy.duplicatePolicy === 'first-wins') continue;
      const idx = entries.findIndex((entry) => entry.range === range);
      if (idx >= 0) entries[idx] = { raw, range, subtags: range === '*' ? [] : range.split('-'), q, position };
      continue;
    }
    seen.add(range);
    entries.push({ raw, range, subtags: range === '*' ? [] : range.split('-'), q, position });
  }

  return { entries, warnings, rejected: false };
}
