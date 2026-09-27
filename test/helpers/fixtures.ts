/**
 * Test helpers: build domain objects straight from JSON fixtures, without
 * touching SQLite or the implementation under test. The hand-authored
 * expected answers live alongside the tests, never produced by the core.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import type { MediaType, Representation, Resource } from '../../src/core/types.js';

export interface RawRepresentation {
  id: string;
  ordinal: number;
  mediaType: string;
  mediaSubtype: string;
  mediaParams: Record<string, string>;
  language: string;
  charset: string;
  body: string;
}

const fixturesDir = resolve(fileURLToPath(new URL('.', import.meta.url)), '..', 'fixtures');

export function loadGridFixture(): Resource {
  const raw = JSON.parse(readFileSync(resolve(fixturesDir, 'grid.json'), 'utf8')) as {
    resource: { id: string; name: string };
    representations: RawRepresentation[];
  };
  const representations: Representation[] = raw.representations
    .slice()
    .sort((a, b) => a.ordinal - b.ordinal)
    .map<Representation>((r) => ({
      id: r.id,
      mediaType: {
        type: r.mediaType,
        subtype: r.mediaSubtype,
        parameters: new Map(Object.entries(r.mediaParams).map(([k, v]) => [k, v])),
      },
      language: r.language,
      charset: r.charset,
      body: r.body,
    }));
  return { id: raw.resource.id, name: raw.resource.name, representations };
}

export function makeMediaType(type: string, subtype: string, params: Record<string, string> = {}): MediaType {
  return { type, subtype, parameters: new Map(Object.entries(params)) };
}

export function makeRepresentation(
  id: string,
  mediaType: MediaType,
  language: string,
  body = id,
): Representation {
  return { id, mediaType, language, charset: 'utf-8', body };
}

export function makeResource(id: string, representations: Representation[]): Resource {
  return { id, name: id, representations };
}
