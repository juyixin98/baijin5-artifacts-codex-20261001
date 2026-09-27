/**
 * SQLite state adapter. Owns the database lifecycle, schema creation and
 * seeding, and maps relational rows into the core domain model. The
 * negotiation core never imports this module, so it stays trivially testable
 * against in-memory fakes.
 */
import { DatabaseSync } from 'node:sqlite';
import { readFileSync } from 'node:fs';
import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';
import type { MediaType, Representation, Resource } from '../core/types.js';

interface ResourceRow {
  id: string;
  name: string;
}

interface RepresentationRow {
  id: string;
  resource_id: string;
  ordinal: number;
  media_type: string;
  media_subtype: string;
  media_params: string;
  language: string;
  charset: string;
  body: string;
}

interface SeedFile {
  resources: Array<{
    id: string;
    name: string;
    representations: Array<{
      ordinal: number;
      mediaType: string;
      mediaSubtype: string;
      mediaParams: Record<string, string>;
      language: string;
      charset: string;
      body: string;
    }>;
  }>;
}

export class ResourceRepository {
  constructor(private readonly db: DatabaseSync) {}

  listResourceIds(): string[] {
    const rows = this.db.prepare('SELECT id FROM resources ORDER BY id').all() as Array<{ id: string }>;
    return rows.map((r) => r.id);
  }

  getResource(id: string): Resource | null {
    const resource = this.db.prepare('SELECT id, name FROM resources WHERE id = ?').get(id) as ResourceRow | undefined;
    if (!resource) return null;

    const rows = this.db
      .prepare('SELECT id, resource_id, ordinal, media_type, media_subtype, media_params, language, charset, body FROM representations WHERE resource_id = ? ORDER BY ordinal, id')
      .all(id) as unknown as RepresentationRow[];

    const representations: Representation[] = rows.map((row) => {
      let rawParams: unknown;
      try {
        rawParams = JSON.parse(row.media_params);
      } catch {
        throw new Error(`Corrupt media_params for representation ${row.id}: not valid JSON`);
      }
      if (typeof rawParams !== 'object' || rawParams === null || Array.isArray(rawParams)) {
        throw new Error(`Corrupt media_params for representation ${row.id}: expected JSON object`);
      }
      const parameters = new Map<string, string>();
      for (const [key, value] of Object.entries(rawParams as Record<string, unknown>)) {
        if (typeof value !== 'string') throw new Error(`Corrupt media_params for ${row.id}: value for ${key} is not a string`);
        parameters.set(key.toLowerCase(), value.toLowerCase());
      }
      const mediaType: MediaType = {
        type: row.media_type.toLowerCase(),
        subtype: row.media_subtype.toLowerCase(),
        parameters,
      };
      return {
        id: row.id,
        mediaType,
        language: row.language.toLowerCase(),
        charset: row.charset.toLowerCase(),
        body: row.body,
      };
    });

    return { id: resource.id, name: resource.name, representations };
  }
}

export interface DatabaseHandle {
  readonly db: DatabaseSync;
  close(): void;
}

export function openDatabase(file: string): DatabaseHandle {
  if (file !== ':memory:') {
    mkdirSync(dirname(file), { recursive: true });
  }
  const db = new DatabaseSync(file);
  db.exec('PRAGMA foreign_keys = ON;');
  return {
    db,
    close: () => db.close(),
  };
}

export function applySchema(db: DatabaseSync, schemaSql: string): void {
  db.exec(schemaSql);
}

export function isSeeded(db: DatabaseSync): boolean {
  const row = db.prepare('SELECT COUNT(*) AS n FROM resources').get() as { n: number };
  return row.n > 0;
}

/** Idempotent insert: existing resources are replaced transactionally. */
export function seedDatabase(db: DatabaseSync, seedPath: string): { resources: number; representations: number } {
  const seed = JSON.parse(readFileSync(seedPath, 'utf8')) as SeedFile;
  const insertResource = db.prepare('INSERT INTO resources (id, name) VALUES (?, ?)');
  const insertRep = db.prepare(
    `INSERT INTO representations
       (id, resource_id, ordinal, media_type, media_subtype, media_params, language, charset, body)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
  );

  let representationCount = 0;
  db.exec('BEGIN');
  try {
    for (const resource of seed.resources) {
      db.prepare('DELETE FROM representations WHERE resource_id = ?').run(resource.id);
      db.prepare('DELETE FROM resources WHERE id = ?').run(resource.id);
      insertResource.run(resource.id, resource.name);
      for (const rep of resource.representations) {
        const repId = `${resource.id}#r${rep.ordinal}`;
        const params = new Map(Object.entries(rep.mediaParams).map(([k, v]) => [k.toLowerCase(), String(v).toLowerCase()]));
        insertRep.run(
          repId,
          resource.id,
          rep.ordinal,
          rep.mediaType.toLowerCase(),
          rep.mediaSubtype.toLowerCase(),
          JSON.stringify(Object.fromEntries(params)),
          rep.language.toLowerCase(),
          (rep.charset ?? 'utf-8').toLowerCase(),
          rep.body,
        );
        representationCount += 1;
      }
    }
    db.exec('COMMIT');
  } catch (err) {
    db.exec('ROLLBACK');
    throw err;
  }

  return { resources: seed.resources.length, representations: representationCount };
}
