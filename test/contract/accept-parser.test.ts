import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { parseAcceptHeader } from '../../src/contract/accept-parser.js';
import { NegotiationError } from '../../src/contract/errors.js';

function expectError(header: string, code: string, policy: 'ignore' | 'reject' = 'ignore'): NegotiationError {
  let caught: unknown;
  try {
    parseAcceptHeader(header, policy);
  } catch (err) {
    caught = err;
  }
  assert.ok(caught instanceof NegotiationError, `expected NegotiationError for "${header}"`);
  assert.equal((caught as NegotiationError).code, code);
  return caught as NegotiationError;
}

describe('parseAcceptHeader - structure', () => {
  it('parses a single exact media type with default quality 1', () => {
    const { ranges } = parseAcceptHeader('application/json');
    assert.equal(ranges.length, 1);
    assert.deepEqual(
      { type: ranges[0]!.type, subtype: ranges[0]!.subtype, quality: ranges[0]!.quality },
      { type: 'application', subtype: 'json', quality: 1 },
    );
  });

  it('parses both wildcard shapes', () => {
    const full = parseAcceptHeader('*/*').ranges[0]!;
    assert.equal(full.wildcardType, true);
    assert.equal(full.wildcardSubtype, true);
    const partial = parseAcceptHeader('text/*').ranges[0]!;
    assert.equal(partial.wildcardType, false);
    assert.equal(partial.wildcardSubtype, true);
    assert.equal(partial.type, 'text');
  });

  it('lower-cases types, subtypes and parameter values', () => {
    const range = parseAcceptHeader('Application/JSON; Version=V2').ranges[0]!;
    assert.equal(range.type, 'application');
    assert.equal(range.subtype, 'json');
    assert.equal(range.constraints.get('version'), 'v2');
  });

  it('tolerates OWS around commas and semicolons', () => {
    const { ranges } = parseAcceptHeader('  application/json ; q=0.5 , text/html ;q=0.1 ');
    assert.equal(ranges.length, 2);
    assert.equal(ranges[0]!.quality, 0.5);
    assert.equal(ranges[1]!.quality, 0.1);
  });
});

describe('parseAcceptHeader - parameters before vs after q', () => {
  it('treats a parameter before q as a match constraint', () => {
    const range = parseAcceptHeader('application/json; version=2; q=0.9').ranges[0]!;
    assert.equal(range.constraints.get('version'), '2');
    assert.equal(range.quality, 0.9);
  });

  it('ignores accept-ext tokens after q by default but records a notice', () => {
    const { ranges, notices } = parseAcceptHeader('text/html; q=0.8; level=1');
    assert.equal(ranges[0]!.quality, 0.8);
    assert.equal(ranges[0]!.constraints.size, 0);
    assert.equal(notices.length, 1);
    assert.equal(notices[0]!.code, 'IGNORED_EXTENSION_PARAMETER');
  });

  it('rejects unknown extensions after q when policy is reject', () => {
    expectError('text/html; q=0.8; level=1', 'UNKNOWN_PARAMETER', 'reject');
  });
});

describe('parseAcceptHeader - weight rules', () => {
  const valid = ['0', '0.0', '0.00', '0.000', '1', '1.0', '1.00', '1.000', '0.5', '0.123', '1', '0.9'];
  for (const w of valid) {
    it(`accepts legal qvalue ${w}`, () => {
      const range = parseAcceptHeader(`text/html;q=${w}`).ranges[0]!;
      assert.ok(range.quality >= 0 && range.quality <= 1);
    });
  }

  const invalid = ['1.5', '2', '-0.1', '0.1234', '1.0000', '1.', '+1', '0x1', 'abc', '1.1', '0.9999'];
  for (const w of invalid) {
    it(`rejects illegal weight ${w} as INVALID_WEIGHT`, () => {
      expectError(`text/html;q=${w}`, 'INVALID_WEIGHT');
    });
  }

  it('treats q=0 as a legal, parseable value (not an error)', () => {
    const range = parseAcceptHeader('text/html;q=0').ranges[0]!;
    assert.equal(range.quality, 0);
  });

  it('rejects a quoted quality value', () => {
    expectError('text/html;q="0.5"', 'INVALID_WEIGHT');
  });
});

describe('parseAcceptHeader - duplicates', () => {
  it('rejects a repeated parameter inside one element', () => {
    expectError('application/json; version=1; version=2', 'DUPLICATE_PARAMETER');
  });

  it('rejects a repeated q parameter', () => {
    expectError('text/html; q=0.5; q=0.8', 'DUPLICATE_PARAMETER');
  });

  it('keeps the first identical range and emits a DUPLICATE_RANGE notice', () => {
    const { ranges, notices } = parseAcceptHeader('text/html, text/html');
    assert.equal(ranges.length, 1);
    assert.equal(notices.length, 1);
    assert.equal(notices[0]!.code, 'DUPLICATE_RANGE');
  });
});

describe('parseAcceptHeader - malformed input', () => {
  const malformed = [
    '',
    ',',
    'application',
    'application/',
    '/json',
    'application/json;',
    'application/json; q',
    'application/json; q=',
    'application/json; =0.5',
    '*/json',
    'application / json',
    'application/json extra',
    'application/json; version',
  ];
  for (const header of malformed) {
    it(`rejects ${JSON.stringify(header)} as MALFORMED_HEADER`, () => {
      expectError(header, 'MALFORMED_HEADER');
    });
  }

  it('preserves header name on the error for tracing', () => {
    const err = expectError('application/json;q=2', 'INVALID_WEIGHT');
    assert.equal(err.headerName, 'Accept');
    assert.equal(err.stage, 'parse-accept');
  });
});
