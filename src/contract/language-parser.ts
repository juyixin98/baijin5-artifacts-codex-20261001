/**
 * RFC 9110 §12.5.3 Accept-Language parser.
 *
 *   Accept-Language = 1#( language-range [ weight ] )
 *   language-range  = "*" / language-tag
 *   language-tag    = 1*8ALPHA *( "-" 1*8alphanum )
 *
 * q=0 explicitly forbids a language. The wildcard "*" matches every tag
 * unmatched by a more specific range. Structural tag violations are
 * MALFORMED_HEADER; bad weights are INVALID_WEIGHT.
 */
import { NegotiationError, type Notice, type NoticeCode } from './errors.js';
import { readToken, skipOWS, splitElements } from './tokenizer.js';
import { parseQValue } from './weight.js';

export interface ParsedLanguageRange {
  readonly index: number;
  readonly raw: string;
  readonly tag: string | null;
  readonly wildcard: boolean;
  readonly quality: number;
  /** Number of subtags; wildcard is 0. Used for longest-match scoring. */
  readonly specificity: number;
}

export interface ParsedAcceptLanguage {
  readonly ranges: readonly ParsedLanguageRange[];
  readonly notices: Notice[];
}

const LANGUAGE_SUBTAG = /^[a-z0-9]{1,8}$/;
const PRIMARY_SUBTAG = /^[a-z]{1,8}$/;

function makeNotice(code: NoticeCode, rangeIndex: number, message: string): Notice {
  return { code, headerName: 'Accept-Language', rangeIndex, message };
}

export function parseAcceptLanguageHeader(header: string): ParsedAcceptLanguage {
  const notices: Notice[] = [];
  const ranges: ParsedLanguageRange[] = [];
  const seen = new Set<string>();

  splitElements(header).forEach((rawElement, rangeIndex) => {
    const element = rawElement.trim();
    if (element === '') {
      throw new NegotiationError(
        'MALFORMED_HEADER',
        'parse-language',
        `Empty language range at position ${rangeIndex + 1}`,
        { headerName: 'Accept-Language', detail: { rangeIndex } },
      );
    }

    let i = 0;
    const tagTok = readToken(element, i);
    if (!tagTok) {
      throw new NegotiationError('MALFORMED_HEADER', 'parse-language', `Malformed language range "${element}"`, {
        headerName: 'Accept-Language',
      });
    }
    const rawTag = tagTok.value.toLowerCase();
    i = tagTok.end;

    let quality: number | undefined;
    if (i < element.length) {
      i = skipOWS(element, i);
      if (element[i] !== ';') {
        throw new NegotiationError(
          'MALFORMED_HEADER',
          'parse-language',
          `Expected ";" or end of range in "${element}"`,
          { headerName: 'Accept-Language' },
        );
      }
      i += 1;
      i = skipOWS(element, i);
      const paramTok = readToken(element, i);
      if (!paramTok || paramTok.value.toLowerCase() !== 'q') {
        throw new NegotiationError(
          'MALFORMED_HEADER',
          'parse-language',
          `Only a quality parameter is allowed on language ranges, in "${element}"`,
          { headerName: 'Accept-Language' },
        );
      }
      i = paramTok.end;
      i = skipOWS(element, i);
      if (element[i] !== '=') {
        throw new NegotiationError('MALFORMED_HEADER', 'parse-language', `Expected "=" after q in "${element}"`, {
          headerName: 'Accept-Language',
        });
      }
      i += 1;
      i = skipOWS(element, i);
      const valueTok = readToken(element, i);
      if (!valueTok || valueTok.end !== element.length) {
        throw new NegotiationError('MALFORMED_HEADER', 'parse-language', `Malformed quality value in "${element}"`, {
          headerName: 'Accept-Language',
        });
      }
      quality = parseQValue(valueTok.value, 'parse-language', 'Accept-Language');
      i = valueTok.end;
      if (i !== element.length) {
        throw new NegotiationError('MALFORMED_HEADER', 'parse-language', `Trailing characters in "${element}"`, {
          headerName: 'Accept-Language',
        });
      }
    }

    const wildcard = rawTag === '*';
    let tag: string | null = null;
    let specificity = 0;
    if (!wildcard) {
      const subtags = rawTag.split('-');
      if (!PRIMARY_SUBTAG.test(subtags[0] as string)) {
        throw new NegotiationError(
          'MALFORMED_HEADER',
          'parse-language',
          `Invalid primary language subtag "${subtags[0]}" in "${element}"`,
          { headerName: 'Accept-Language' },
        );
      }
      for (const sub of subtags.slice(1)) {
        if (!LANGUAGE_SUBTAG.test(sub)) {
          throw new NegotiationError(
            'MALFORMED_HEADER',
            'parse-language',
            `Invalid language subtag "${sub}" in "${element}" (expected 1-8 alphanumeric characters)`,
            { headerName: 'Accept-Language' },
          );
        }
      }
      tag = subtags.join('-');
      specificity = subtags.length;
    }

    // A repeated tag is a duplicate regardless of its q value; the first
    // occurrence wins (same policy as Accept media ranges).
    const key = wildcard ? '*' : `tag:${tag}`;
    if (seen.has(key)) {
      notices.push(makeNotice('DUPLICATE_RANGE', rangeIndex, `Duplicate language range "${element}" ignored; first occurrence wins`));
      return;
    }
    seen.add(key);

    ranges.push({
      index: rangeIndex,
      raw: element,
      tag,
      wildcard,
      quality: quality ?? 1,
      specificity,
    });
  });

  return { ranges, notices };
}
