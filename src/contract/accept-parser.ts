/**
 * RFC 9110 §12.5.1 Accept header parser.
 *
 * Grammar honored:
 *   Accept       = #media-range
 *   media-range  = ( star / star ) / ( type / star ) / ( type / subtype )
 *                  with each shape written canonically as type "/" subtype,
 *                  where a literal "*" token is the wildcard
 *                  *( OWS ";" OWS parameter ) [ weight ] *( OWS ";" OWS accept-ext )
 *   parameter    = token "=" ( token / quoted-string )
 *   weight       = OWS ";" OWS "q=" qvalue
 *   accept-ext   = token [ "=" ( token / quoted-string ) ]
 *
 * Policy decisions (all traceable):
 *  - Parameters BEFORE q are MATCH CONSTRAINTS: a range only matches a
 *    representation whose media type carries every same-named parameter.
 *  - Extensions AFTER q are governed by config unknownParameters
 *    ("ignore" -> IGNORED_EXTENSION_PARAMETER notice, "reject" -> 400).
 *  - A parameter name repeated inside one element is always 400
 *    DUPLICATE_PARAMETER; an identical range repeated across elements is a
 *    DUPLICATE_RANGE notice with the FIRST occurrence kept (deterministic).
 */
import { NegotiationError, type Notice, type NoticeCode } from './errors.js';
import { readQuotedString, readToken, skipOWS, splitElements } from './tokenizer.js';
import { parseQValue } from './weight.js';
import type { MediaType } from '../core/types.js';

export type UnknownParameterPolicy = 'ignore' | 'reject';

export interface ParsedAcceptRange {
  readonly index: number;
  readonly raw: string;
  readonly type: string;
  readonly subtype: string;
  readonly wildcardType: boolean;
  readonly wildcardSubtype: boolean;
  /** Match constraints (parameters declared before q). */
  readonly constraints: ReadonlyMap<string, string>;
  readonly quality: number;
}

export interface ParsedAccept {
  readonly ranges: readonly ParsedAcceptRange[];
  readonly notices: Notice[];
}

/**
 * Parse one "parameter / weight / extension" list for an element.
 * `pos` points just after the "type/subtype" token.
 */
function parseSemicolonList(
  element: string,
  pos: number,
  rangeIndex: number,
  unknownParameters: UnknownParameterPolicy,
  notices: Notice[],
): { constraints: Map<string, string>; quality: number | undefined; end: number } {
  const constraints = new Map<string, string>();
  let quality: number | undefined;
  let sawQ = false;
  let i = pos;

  while (i < element.length) {
    i = skipOWS(element, i);
    if (i >= element.length) break;
    if (element[i] !== ';') {
      throw new NegotiationError('MALFORMED_HEADER', 'parse-accept', `Expected ";" at position ${i} in "${element}"`, {
        headerName: 'Accept',
      });
    }
    i += 1;
    i = skipOWS(element, i);
    const nameTok = readToken(element, i);
    if (!nameTok) {
      throw new NegotiationError('MALFORMED_HEADER', 'parse-accept', `Expected parameter name at position ${i} in "${element}"`, {
        headerName: 'Accept',
      });
    }
    const name = nameTok.value.toLowerCase();
    i = nameTok.end;
    i = skipOWS(element, i);
    if (element[i] !== '=') {
      throw new NegotiationError('MALFORMED_HEADER', 'parse-accept', `Expected "=" after parameter "${name}" in "${element}"`, {
        headerName: 'Accept',
      });
    }
    i += 1;
    i = skipOWS(element, i);
    const quoted = readQuotedString(element, i);
    let value: string;
    if (quoted) {
      value = quoted.value;
      i = quoted.end;
    } else {
      const valueTok = readToken(element, i);
      if (!valueTok) {
        throw new NegotiationError('MALFORMED_HEADER', 'parse-accept', `Expected parameter value in "${element}"`, {
          headerName: 'Accept',
        });
      }
      value = valueTok.value;
      i = valueTok.end;
    }

    if (name === 'q') {
      if (sawQ) {
        throw new NegotiationError('DUPLICATE_PARAMETER', 'parse-accept', `Duplicate quality parameter "q" in range #${rangeIndex + 1}`, {
          headerName: 'Accept',
          detail: { rangeIndex },
        });
      }
      // q takes a bare qvalue token: quoted "0.5" is not legal here.
      if (quoted) {
        throw new NegotiationError('INVALID_WEIGHT', 'parse-accept', `Quoted quality value is not allowed in range #${rangeIndex + 1}`, {
          headerName: 'Accept',
        });
      }
      quality = parseQValue(value, 'parse-accept', 'Accept');
      sawQ = true;
      continue;
    }

    if (sawQ) {
      // Grammar: only accept-ext may follow the weight.
      if (unknownParameters === 'reject') {
        throw new NegotiationError(
          'UNKNOWN_PARAMETER',
          'parse-accept',
          `Unknown Accept extension parameter "${name}" after quality in range #${rangeIndex + 1}`,
          { headerName: 'Accept', detail: { parameter: name, rangeIndex } },
        );
      }
      notices.push(makeNotice('IGNORED_EXTENSION_PARAMETER', rangeIndex, `Accept extension "${name}" after q is not implemented and was ignored`));
      continue;
    }

    if (constraints.has(name)) {
      throw new NegotiationError(
        'DUPLICATE_PARAMETER',
        'parse-accept',
        `Duplicate media parameter "${name}" in range #${rangeIndex + 1}`,
        { headerName: 'Accept', detail: { parameter: name, rangeIndex } },
      );
    }
    constraints.set(name, value.toLowerCase());
  }

  // The scan loop terminates exactly at end-of-element; every character was
  // consumed as either OWS, a delimiter or a validated token.
  return { constraints, quality, end: i };
}

function makeNotice(code: NoticeCode, rangeIndex: number, message: string): Notice {
  return { code, headerName: 'Accept', rangeIndex, message };
}

function rangeKey(type: string, subtype: string, constraints: ReadonlyMap<string, string>): string {
  const params = [...constraints.entries()].map(([k, v]) => `${k}=${v}`).join('&');
  return `${type}/${subtype}${params ? `;${params}` : ''}`;
}

export function parseAcceptHeader(header: string, unknownParameters: UnknownParameterPolicy = 'ignore'): ParsedAccept {
  const notices: Notice[] = [];
  const ranges: ParsedAcceptRange[] = [];
  const seen = new Set<string>();

  splitElements(header).forEach((rawElement, rangeIndex) => {
    const element = rawElement.trim();
    if (element === '') {
      throw new NegotiationError(
        'MALFORMED_HEADER',
        'parse-accept',
        `Empty media range at position ${rangeIndex + 1}`,
        { headerName: 'Accept', detail: { rangeIndex } },
      );
    }

    // Read type "/" subtype (with wildcards) as the first token pair.
    const typeTok = readToken(element, 0);
    if (!typeTok || typeTok.end === element.length || element[typeTok.end] !== '/') {
      throw new NegotiationError('MALFORMED_HEADER', 'parse-accept', `Malformed media range "${element}": expected type "/" subtype`, {
        headerName: 'Accept',
      });
    }
    const type = typeTok.value.toLowerCase();
    if (type.includes('"')) {
      throw new NegotiationError('MALFORMED_HEADER', 'parse-accept', `Malformed type token in "${element}"`, { headerName: 'Accept' });
    }
    let i = typeTok.end + 1;
    const subtypeTok = readToken(element, i);
    if (!subtypeTok) {
      throw new NegotiationError('MALFORMED_HEADER', 'parse-accept', `Missing subtype in media range "${element}"`, {
        headerName: 'Accept',
      });
    }
    i = subtypeTok.end;
    const subtype = subtypeTok.value.toLowerCase();

    // The only wildcard shapes are "*/*" and "type/*".
    const wildcardType = type === '*';
    const wildcardSubtype = subtype === '*';
    if (wildcardType && subtype !== '*') {
      throw new NegotiationError('MALFORMED_HEADER', 'parse-accept', `Wildcard type requires wildcard subtype in "${element}"`, {
        headerName: 'Accept',
      });
    }
    if (!wildcardType && !/^[a-z0-9][a-z0-9!#$&^_.+-]*$/.test(type)) {
      throw new NegotiationError('MALFORMED_HEADER', 'parse-accept', `Invalid type token "${type}"`, { headerName: 'Accept' });
    }
    if (!wildcardSubtype && !/^[a-z0-9][a-z0-9!#$&^_.+-]*$/.test(subtype)) {
      throw new NegotiationError('MALFORMED_HEADER', 'parse-accept', `Invalid subtype token "${subtype}"`, {
        headerName: 'Accept',
      });
    }

    const { constraints, quality } = parseSemicolonList(element, i, rangeIndex, unknownParameters, notices);

    const key = rangeKey(wildcardType ? '*' : type, wildcardSubtype ? '*' : subtype, constraints);
    if (seen.has(key)) {
      notices.push(makeNotice('DUPLICATE_RANGE', rangeIndex, `Duplicate media range "${key}" ignored; first occurrence wins`));
      return;
    }
    seen.add(key);

    ranges.push({
      index: rangeIndex,
      raw: element,
      type: wildcardType ? '*' : type,
      subtype: wildcardSubtype ? '*' : subtype,
      wildcardType,
      wildcardSubtype,
      constraints,
      quality: quality ?? 1,
    });
  });

  return { ranges, notices };
}

/** Render a {@link MediaType} as a canonical content type string. */
export function formatMediaType(mt: MediaType): string {
  const params = [...mt.parameters.entries()].map(([k, v]) => `; ${k}=${v}`).join('');
  return `${mt.type}/${mt.subtype}${params}`;
}
