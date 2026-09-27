import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { negotiate, type NegotiationConfig } from '../../src/core/negotiator.js';
import { loadGridFixture, makeMediaType, makeRepresentation, makeResource } from '../helpers/fixtures.js';
import { serviceVersion } from '../../src/core/version.js';

const grid = loadGridFixture();

/** Run negotiation and assert a concrete winner id + failure category. */
function winner(accept: string | null, lang: string | null, config?: Partial<NegotiationConfig>): string {
  const trace = negotiate({ resource: grid, acceptHeader: accept, acceptLanguageHeader: lang, config });
  assert.ok(trace.failure === null, `expected a winner but got ${trace.failure?.code}: ${trace.failure?.message}`);
  return trace.winner!.representationId;
}

function failureCode(accept: string | null, lang: string | null, config?: Partial<NegotiationConfig>): string {
  const trace = negotiate({ resource: grid, acceptHeader: accept, acceptLanguageHeader: lang, config });
  assert.ok(trace.failure !== null, 'expected failure but negotiation succeeded');
  return trace.failure.code;
}

describe('negotiator - hand-computed media selection on the grid', () => {
  it('exact media + exact language selects v0', () => {
    assert.equal(winner('application/json', 'en'), 'v0');
  });

  it('exact language beats a prefix match at equal quality (v1 en over v2 en-gb)', () => {
    assert.equal(winner('application/vnd.shop.v2+json', 'en'), 'v1');
  });

  it('requested regional tag selects the regional variant when served (en-gb -> v2)', () => {
    assert.equal(winner('application/vnd.shop.v2+json', 'en-gb'), 'v2');
  });

  it('higher quality media range wins regardless of list position (xml q1 over json q0.5 -> v4)', () => {
    assert.equal(winner('application/xml, application/json;q=0.5', 'en'), 'v4');
  });

  it('a concrete range outranks a later wildcard even at equal structure (v4 over wildcard q0.5)', () => {
    assert.equal(winner('application/xml;q=0.9, */*;q=0.9', 'en'), 'v4');
  });

  it('type wildcard application/* admits every application subtype; earliest ordered v0 wins', () => {
    // v0 (json;version=1), v1 (vnd...), v4 (xml) all match the unconstrained
    // type wildcard at specificity 1 with language "en" exact; declaration
    // order breaks the tie -> v0.
    assert.equal(winner('application/*', 'en'), 'v0');
  });

  it('a parameter constraint on a type wildcard narrows it (application/*;version=1 -> v0)', () => {
    assert.equal(winner('application/*; version=1', 'en'), 'v0');
  });
});

describe('negotiator - wildcard priority and explicit q=0 prohibition', () => {
  it('q=0 on text/html forbids only html while */* still admits json (v0)', () => {
    // v6/v7 match the specific q=0 range (specificity 2) and are forbidden;
    // json/xml match the wildcard and survive.
    assert.equal(winner('text/html;q=0, */*;q=1', 'en'), 'v0');
  });

  it('q=0 on application/json forbids json but admits other application subtypes (v1, earliest admissible)', () => {
    // v0 matches the specific application/json;q=0 range and is prohibited;
    // v1 (application/vnd.shop.v2+json) does NOT match application/json, so it
    // falls through to */*;q=1 and wins on declaration order over xml/plain.
    assert.equal(winner('application/json;q=0, */*;q=1', 'en'), 'v1');
  });

  it('q=0 on both json families falls through to xml (v4)', () => {
    assert.equal(winner('application/json;q=0, application/vnd.shop.v2+json;q=0, */*;q=1', 'en'), 'v4');
  });

  it('fails UNACCEPTABLE_MEDIA_TYPE when every media type is explicitly prohibited', () => {
    const code = failureCode('application/json;q=0, application/*;q=0, text/*;q=0, */*;q=0', 'en');
    assert.equal(code, 'UNACCEPTABLE_MEDIA_TYPE');
  });

  it('fails UNACCEPTABLE_MEDIA_TYPE when no offered media type matches at all', () => {
    assert.equal(failureCode('image/png, image/jpeg', 'en'), 'UNACCEPTABLE_MEDIA_TYPE');
  });

  it('records q=0 explicitly in the per-candidate reason', () => {
    const trace = negotiate({ resource: grid, acceptHeader: 'text/html;q=0, */*', acceptLanguageHeader: 'en' });
    const htmlScore = trace.candidateScores.find((s) => s.representationId === 'v6')!;
    assert.equal(htmlScore.combined, 0);
    assert.match(htmlScore.reason, /EXPLICITLY FORBIDDEN by media q=0/);
  });
});

describe('negotiator - language axis negotiated independently', () => {
  it('fails UNACCEPTABLE_LANGUAGE when media is fine but no language matches', () => {
    assert.equal(failureCode('application/json', 'ja'), 'UNACCEPTABLE_LANGUAGE');
  });

  it('fails UNACCEPTABLE_LANGUAGE when every matching language carries q=0 (en prefix also bans en-gb)', () => {
    assert.equal(failureCode('*/*', 'en;q=0, fr;q=0'), 'UNACCEPTABLE_LANGUAGE');
  });

  it('truncation fallback serves an ancestor tag (en-us requested, en served -> v0)', () => {
    assert.equal(winner('application/json', 'en-us'), 'v0');
  });

  it('strict filtering policy disables ancestor fallback (en-us -> failure)', () => {
    assert.equal(failureCode('application/json', 'en-us', { languageFallback: 'filtering' }), 'UNACCEPTABLE_LANGUAGE');
  });

  it('marks truncation fallback in the trace reason', () => {
    const trace = negotiate({ resource: grid, acceptHeader: 'application/json', acceptLanguageHeader: 'en-us' });
    const score = trace.candidateScores.find((s) => s.representationId === 'v0')!;
    assert.match(score.reason, /truncation fallback/);
  });

  it('higher language quality selects fr variant v3 over en', () => {
    assert.equal(winner('application/vnd.shop.v2+json', 'fr;q=0.9, en;q=0.8'), 'v3');
  });
});

describe('negotiator - parameter constraints', () => {
  it('matches a media parameter declared before q (version=1 -> v0)', () => {
    assert.equal(winner('application/json; version=1', 'en'), 'v0');
  });

  it('fails when the parameter value is not offered (version=2)', () => {
    assert.equal(failureCode('application/json; version=2', 'en'), 'UNACCEPTABLE_MEDIA_TYPE');
  });

  it('applies a parameter constraint through a type wildcard (application/*;version=1 -> v0)', () => {
    assert.equal(winner('application/*; version=1', 'en'), 'v0');
  });
});

describe('negotiator - separate-axis combination failure', () => {
  it('returns NO_VARIANT_FOR_COMBINATION when media and language allow disjoint variants', () => {
    const resource = makeResource('split', [
      makeRepresentation('a', makeMediaType('application', 'json'), 'en'),
      makeRepresentation('b', makeMediaType('application', 'xml'), 'fr'),
    ]);
    const trace = negotiate({ resource, acceptHeader: 'application/json', acceptLanguageHeader: 'fr' });
    assert.ok(trace.failure);
    assert.equal(trace.failure.code, 'NO_VARIANT_FOR_COMBINATION');
    assert.equal(trace.failure.stage, 'combine');
  });
});

describe('negotiator - absent headers', () => {
  it('absent Accept behaves as */* and still negotiates language', () => {
    // Wildcard media; fr + en both q1 -> earliest fr variant is v3 (order 3)
    // versus v5/v7; v3 has the lowest ordinal among fr representations.
    assert.equal(winner(null, 'fr'), 'v3');
  });

  it('both headers absent selects the first declared variant deterministically (v0)', () => {
    for (let i = 0; i < 10; i += 1) {
      assert.equal(winner(null, null), 'v0');
    }
  });
});

describe('negotiator - stable same-weight tie breaking', () => {
  it('breaks ties by declaration order then id and is reproducible', () => {
    const resource = makeResource('tie', [
      makeRepresentation('x', makeMediaType('application', 'json'), 'en'),
      makeRepresentation('y', makeMediaType('application', 'json'), 'en'),
    ]);
    for (let i = 0; i < 10; i += 1) {
      const trace = negotiate({ resource, acceptHeader: '*/*', acceptLanguageHeader: '*' });
      assert.equal(trace.winner!.representationId, 'x');
    }
  });
});

describe('negotiator - Vary reflects only axes that can influence the response', () => {
  it('varies on both axes for the multi-variant grid when headers are present', () => {
    const trace = negotiate({ resource: grid, acceptHeader: '*/*', acceptLanguageHeader: 'en' });
    assert.deepEqual(trace.vary, ['Accept', 'Accept-Language']);
  });

  it('omits Accept on a language-only-varying resource when Accept is absent', () => {
    const resource = makeResource('langonly', [
      makeRepresentation('en', makeMediaType('application', 'json'), 'en'),
      makeRepresentation('fr', makeMediaType('application', 'json'), 'fr'),
    ]);
    const trace = negotiate({ resource, acceptHeader: null, acceptLanguageHeader: null });
    assert.deepEqual(trace.vary, ['Accept-Language']);
    assert.equal(trace.winner!.representationId, 'en');
  });

  it('emits an empty Vary on a single-variant resource with no headers', () => {
    const resource = makeResource('one', [makeRepresentation('only', makeMediaType('application', 'json'), 'en')]);
    const trace = negotiate({ resource, acceptHeader: null, acceptLanguageHeader: null });
    assert.deepEqual(trace.vary, []);
  });

  it('always lists a header that was actually sent, even on a single-axis resource', () => {
    const resource = makeResource('langonly', [
      makeRepresentation('en', makeMediaType('application', 'json'), 'en'),
      makeRepresentation('fr', makeMediaType('application', 'json'), 'fr'),
    ]);
    const trace = negotiate({ resource, acceptHeader: 'application/json', acceptLanguageHeader: null });
    assert.deepEqual(trace.vary, ['Accept', 'Accept-Language']);
  });
});

describe('negotiator - parse failure classification and trace metadata', () => {
  it('surfaces INVALID_WEIGHT with the parse stage instead of negotiating', () => {
    const trace = negotiate({ resource: grid, acceptHeader: 'text/html;q=1.5', acceptLanguageHeader: 'en' });
    assert.ok(trace.failure);
    assert.equal(trace.failure.code, 'INVALID_WEIGHT');
    assert.equal(trace.failure.stage, 'parse-accept');
    assert.deepEqual(trace.vary, ['Accept']);
  });

  it('surfaces a malformed language header on the language axis', () => {
    const trace = negotiate({ resource: grid, acceptHeader: '*/*', acceptLanguageHeader: 'en-' });
    assert.equal(trace.failure!.code, 'MALFORMED_HEADER');
    assert.equal(trace.failure!.stage, 'parse-language');
    assert.deepEqual(trace.vary, ['Accept-Language']);
  });

  it('trace carries run identity, version, inputs, scores for every variant and ordered steps', () => {
    const trace = negotiate({ resource: grid, acceptHeader: 'application/json', acceptLanguageHeader: 'en' });
    assert.match(trace.runId, /^[0-9a-f-]{36}$/);
    assert.equal(trace.serviceVersion, serviceVersion());
    assert.equal(trace.serviceVersion, '1.0.0');
    assert.equal(trace.acceptHeaderRaw, 'application/json');
    assert.equal(trace.candidateScores.length, grid.representations.length);
    assert.ok(trace.steps.some((s) => s.startsWith('parse:')));
    assert.ok(trace.steps.some((s) => s.startsWith('score:')));
    assert.ok(trace.steps.some((s) => s.startsWith('verdict: WINNER')));
    assert.ok(trace.elapsedMs >= 0);
  });
});
