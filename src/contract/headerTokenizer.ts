/**
 * RFC 9110 §5.6-ish tokenizer shared by the Accept / Accept-Language parsers.
 *
 * Splits a comma-separated header value into raw items while respecting
 * quoted-string parameter values (a comma inside quotes is not a separator).
 * This module performs NO semantic validation: that is the job of the
 * specific parsers, so each parser owns its failure categories.
 */

export interface RawParameter {
  name: string;
  value: string;
  /** Raw `name=value` text as it appeared (trimmed). */
  raw: string;
}

export interface RawItem {
  /** Everything before the first ';', trimmed. */
  main: string;
  /** Parameters in declaration order (names preserved as-written). */
  params: RawParameter[];
  /** The full raw item, trimmed. */
  raw: string;
}

const TOKEN_CHAR = /^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$/;

export function isToken(value: string): boolean {
  return TOKEN_CHAR.test(value);
}

/**
 * Split an HTTP structured list header on commas, quote-aware.
 * Backslash-escaped chars inside quoted strings are handled (their quote
 * does not terminate the string).
 */
export function splitItems(header: string): string[] {
  const items: string[] = [];
  let current = '';
  let inQuotes = false;
  for (let i = 0; i < header.length; i++) {
    const ch = header[i]!;
    if (inQuotes) {
      if (ch === '\\') {
        current += ch + (header[i + 1] ?? '');
        i++;
        continue;
      }
      if (ch === '"') inQuotes = false;
      current += ch;
    } else if (ch === '"') {
      inQuotes = true;
      current += ch;
    } else if (ch === ',') {
      items.push(current);
      current = '';
    } else {
      current += ch;
    }
  }
  items.push(current);
  return items.map((item) => item.trim()).filter((item) => item.length > 0);
}

/** Unquote a quoted-string parameter value, undo backslash escapes. */
export function unquote(value: string): string {
  if (value.length >= 2 && value.startsWith('"') && value.endsWith('"')) {
    return value
      .slice(1, -1)
      .replace(/\\(.)/g, '$1');
  }
  return value;
}

/**
 * Parse one comma-separated item into `main` + ordered parameters.
 * Does not validate names/values; callers decide what is an error.
 */
export function parseItem(rawItem: string): RawItem {
  const segments = rawItem.split(';');
  const main = (segments.shift() ?? '').trim();
  const params: RawParameter[] = [];
  for (const segment of segments) {
    const trimmed = segment.trim();
    if (trimmed === '') continue;
    const eq = trimmed.indexOf('=');
    if (eq === -1) {
      params.push({ name: trimmed, value: '', raw: trimmed });
      continue;
    }
    const name = trimmed.slice(0, eq).trim();
    const value = trimmed.slice(eq + 1).trim();
    params.push({ name, value: unquote(value), raw: trimmed });
  }
  return { main, params, raw: rawItem.trim() };
}

/**
 * Validate a q-value string per RFC 9110 §12.4.2:
 *   qvalue = ( "0" [ "." 0*3DIGIT ] ) / ( "1" [ "." 0*3("0") ] )
 * Returns the numeric weight, or null when the string is not a legal qvalue.
 */
export function parseQValue(raw: string): number | null {
  if (!/^(?:0(?:\.\d{0,3})?|1(?:\.0{0,3})?)$/.test(raw)) return null;
  return Number(raw);
}
