import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { parseAcceptLanguageHeader } from '../../src/contract/language-parser.js';
import { NegotiationError } from '../../src/contract/errors.js';

function expectError(header: string): NegotiationError {
  let caught: unknown;
  try {
    parseAcceptLanguageHeader(header);
  } catch (err) {
    caught = err;
  }
  assert.ok(caught instanceof NegotiationError, `expected NegotiationError for ${JSON.stringify(header)}`);
  return caught as NegotiationError;
}

describe('parseAcceptLanguageHeader - structure', () => {
  it('parses a tag with implicit quality 1 and correct specificity', () => {
    const range = parseAcceptLanguageHeader('en-gb').ranges[0]!;
    assert.equal(range.tag, 'en-gb');
    assert.equal(range.specificity, 2);
    assert.equal(range.quality, 1);
    assert.equal(range.wildcard, false);
  });

  it('parses the standalone wildcard', () => {
    const range = parseAcceptLanguageHeader('*').ranges[0]!;
    assert.equal(range.wildcard, true);
    assert.equal(range.tag, null);
    assert.equal(range.specificity, 0);
  });

  it('parses an explicit quality', () => {
    const range = parseAcceptLanguageHeader('fr;q=0.3').ranges[0]!;
    assert.equal(range.tag, 'fr');
    assert.equal(range.quality, 0.3);
  });

  it('parses an ordered list with OWS', () => {
    const { ranges } = parseAcceptLanguageHeader(' en ; q=1 , fr ;  q=0.8 , de;q=0.5 ');
    assert.deepEqual(ranges.map((r) => r.tag), ['en', 'fr', 'de']);
    assert.deepEqual(ranges.map((r) => r.quality), [1, 0.8, 0.5]);
  });
});

describe('parseAcceptLanguageHeader - weights and tag validity', () => {
  for (const w of ['1.1', '0.1234', '-1', '2', '0.', '1.01']) {
    it(`rejects bad weight ${w} as INVALID_WEIGHT`, () => {
      assert.equal(expectError(`en;q=${w}`).code, 'INVALID_WEIGHT');
    });
  }

  it('accepts q=0 as a legal prohibition value', () => {
    assert.equal(parseAcceptLanguageHeader('en;q=0').ranges[0]!.quality, 0);
  });

  const badTags = ['123', 'en-', '-en', 'en-us-', 'en-TOOLONGSUB', 'en--us', '12-en', 'en-123456789'];
  for (const tag of badTags) {
    it(`rejects malformed tag ${JSON.stringify(tag)} as MALFORMED_HEADER`, () => {
      assert.equal(expectError(tag).code, 'MALFORMED_HEADER');
    });
  }

  it('accepts structurally legal 1..8 alpha primary subtags', () => {
    assert.equal(parseAcceptLanguageHeader('e').ranges[0]!.tag, 'e');
    assert.equal(parseAcceptLanguageHeader('england').ranges[0]!.tag, 'england');
    assert.equal(parseAcceptLanguageHeader('xx').ranges[0]!.tag, 'xx');
  });

  it('rejects any parameter other than q', () => {
    assert.equal(expectError('en;level=1').code, 'MALFORMED_HEADER');
  });
});

describe('parseAcceptLanguageHeader - duplicates', () => {
  it('keeps the first of a repeated tag even when q differs', () => {
    const { ranges, notices } = parseAcceptLanguageHeader('en;q=0.9, en;q=0.2');
    assert.equal(ranges.length, 1);
    assert.equal(ranges[0]!.quality, 0.9);
    assert.equal(notices[0]!.code, 'DUPLICATE_RANGE');
  });

  it('treats wildcard duplicates consistently', () => {
    const { ranges, notices } = parseAcceptLanguageHeader('*, *;q=0.1');
    assert.equal(ranges.length, 1);
    assert.equal(ranges[0]!.quality, 1);
    assert.equal(notices.length, 1);
  });
});
