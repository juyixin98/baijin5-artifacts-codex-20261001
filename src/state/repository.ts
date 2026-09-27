/**
 * SQLite state adapter.
 *
 * The kernel talks only to the Candidate interface; this module owns the
 * persistence shape and maps rows to/from it. Uses Node's built-in
 * node:sqlite (locked runtime dependency — see README), so the data layer
 * has no third-party native module.
 */

import { DatabaseSync } from 'node:sqlite';
import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';
import type { Candidate } from '../contract/types.js';
import { FIXTURES, type FixtureResource } from './fixtures.js';

export interface StoredRepresentation extends Candidate {
  body: string;
}

export interface StoredResource {
  id: string;
  path: string;
  title: string;
  representations: StoredRepresentation[];
}

export class ResourceRepository {
  constructor(private readonly db: DatabaseSync) {}

  static open(path: string): ResourceRepository {
    if (path !== ':memory:') mkdirSync(dirname(path), { recursive: true });
    const db = new DatabaseSync(path);
    db.exec('PRAGMA journal_mode = WAL;');
    db.exec(`
      CREATE TABLE IF NOT EXISTS resources (
        id    TEXT PRIMARY KEY,
        path  TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS representations (
        id          TEXT PRIMARY KEY,
        resource_id TEXT NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
        ordinal     INTEGER NOT NULL,
        media_type  TEXT NOT NULL,
        media_subtype TEXT NOT NULL,
        media_params TEXT NOT NULL DEFAULT '{}',
        language    TEXT NOT NULL,
        body        TEXT NOT NULL,
        UNIQUE(resource_id, ordinal)
      );
    `);
    return new ResourceRepository(db);
  }

  isSeeded(): boolean {
    const row = this.db.prepare('SELECT COUNT(*) AS n FROM resources').get() as { n: number };
    return row.n > 0;
  }

  seed(fixtures: FixtureResource[] = FIXTURES): void {
    const insertResource = this.db.prepare('INSERT INTO resources (id, path, title) VALUES (?, ?, ?)');
    const insertRep = this.db.prepare(
      `INSERT INTO representations
         (id, resource_id, ordinal, media_type, media_subtype, media_params, language, body)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
    );
    const seedOne = (resource: FixtureResource): void => {
      insertResource.run(resource.id, resource.path, resource.title);
      resource.representations.forEach((rep, ordinal) => {
        const [type, subtype] = rep.mediaType.split('/') as [string, string];
        const params = Object.fromEntries(
          Object.entries(rep.mediaParams).map(([k, v]) => [k.toLowerCase(), v.toLowerCase()]),
        );
        insertRep.run(
          rep.id,
          resource.id,
          ordinal,
          type.toLowerCase(),
          subtype.toLowerCase(),
          JSON.stringify(params),
          rep.language.toLowerCase(),
          rep.body,
        );
      });
    };
    this.db.exec('BEGIN');
    try {
      for (const resource of fixtures) seedOne(resource);
      this.db.exec('COMMIT');
    } catch (error) {
      this.db.exec('ROLLBACK');
      throw error;
    }
  }

  /** Throws ReferenceError for unknown paths; the HTTP layer maps it to 404. */
  getByPath(path: string): StoredResource {
    const resource = this.db.prepare('SELECT id, path, title FROM resources WHERE path = ?').get(path) as
      | { id: string; path: string; title: string }
      | undefined;
    if (!resource) throw new ReferenceError(`unknown resource path: ${path}`);

    const rows = this.db
      .prepare(
        `SELECT id, ordinal, media_type, media_subtype, media_params, language, body
           FROM representations WHERE resource_id = ? ORDER BY ordinal ASC`,
      )
      .all(resource.id) as Array<{
      id: string;
      ordinal: number;
      media_type: string;
      media_subtype: string;
      media_params: string;
      language: string;
      body: string;
    }>;

    return {
      ...resource,
      representations: rows.map((row) => ({
        id: row.id,
        ordinal: row.ordinal,
        type: row.media_type,
        subtype: row.media_subtype,
        params: JSON.parse(row.media_params) as Record<string, string>,
        language: row.language,
        body: row.body,
      })),
    };
  }

  listPaths(): string[] {
    const rows = this.db.prepare('SELECT path FROM resources ORDER BY path ASC').all() as Array<{ path: string }>;
    return rows.map((row) => row.path);
  }

  close(): void {
    this.db.close();
  }
}
