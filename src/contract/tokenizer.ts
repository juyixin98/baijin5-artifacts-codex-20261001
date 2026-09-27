/**
 * Low level RFC 9110 token / quoted-string / OWS scanning primitives.
 *
 * This module does string handling only. It knows nothing about media
 * types or weights, so both parsers share one audited tokenizer.
 */

export interface ScanResult {
  /** Characters consumed, not including surrounding OWS when applicable. */
  readonly end: number;
}

export const TOKEN_CHARS = new Set(
  "!#$%&'*+-.^_`|~0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
);

export function isTokenChar(ch: string | undefined): boolean {
  return ch !== undefined && TOKEN_CHARS.has(ch);
}

export function isOWS(ch: string | undefined): boolean {
  return ch === ' ' || ch === '\t';
}

/** Advance index past optional whitespace (space / HTAB). */
export function skipOWS(input: string, pos: number): number {
  let i = pos;
  while (i < input.length && isOWS(input[i])) i += 1;
  return i;
}

/** Read a single RFC 8941-style token; returns null when no token starts. */
export function readToken(input: string, start: number): { value: string; end: number } | null {
  let i = start;
  while (i < input.length && isTokenChar(input[i])) i += 1;
  if (i === start) return null;
  return { value: input.slice(start, i), end: i };
}

/**
 * Read a quoted-string (RFC 9110 §5.6.4): double quotes, backslash-quoted-pair
 * escapes, tab / printable ASCII only. obs-text is rejected deliberately so
 * negotiation inputs stay ASCII deterministic.
 */
export function readQuotedString(input: string, start: number): { value: string; end: number } | null {
  if (input[start] !== '"') return null;
  let i = start + 1;
  let value = '';
  while (i < input.length) {
    const ch = input[i] as string;
    if (ch === '"') return { value, end: i + 1 };
    if (ch === '\\') {
      const next = input[i + 1];
      if (next === undefined) return null;
      // quoted-pair may escape HTAB, SP and VCHAR.
      if (next !== '\t' && next !== ' ' && (next.charCodeAt(0) < 0x21 || next.charCodeAt(0) > 0x7e)) {
        return null;
      }
      value += next;
      i += 2;
      continue;
    }
    if (ch === '\t' || (ch.charCodeAt(0) >= 0x20 && ch.charCodeAt(0) <= 0x7e)) {
      value += ch;
      i += 1;
      continue;
    }
    return null;
  }
  return null;
}

/**
 * Split a comma separated header into raw element strings, respecting quoted
 * strings so commas inside quotes do not split elements. Empty elements are
 * preserved as '' and reported by callers as malformed input.
 */
export function splitElements(header: string): readonly string[] {
  const elements: string[] = [];
  let current = '';
  let inQuotes = false;
  for (let i = 0; i < header.length; i += 1) {
    const ch = header[i] as string;
    if (inQuotes) {
      current += ch;
      if (ch === '\\' && i + 1 < header.length) {
        current += header[i + 1] as string;
        i += 1;
      } else if (ch === '"') {
        inQuotes = false;
      }
      continue;
    }
    if (ch === '"') {
      inQuotes = true;
      current += ch;
    } else if (ch === ',') {
      elements.push(current);
      current = '';
    } else {
      current += ch;
    }
  }
  elements.push(current);
  return elements;
}
