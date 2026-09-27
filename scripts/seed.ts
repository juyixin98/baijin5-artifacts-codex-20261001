/**
 * Standalone seeding utility: creates the schema and (re)seeds the database
 * from data/seed.json. Usage: npm run seed [-- --db path] [--reset].
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { applySchema, openDatabase, seedDatabase } from '../src/state/repository.js';

const args = process.argv.slice(2);
const reset = args.includes('--reset');
const dbFlagIndex = args.indexOf('--db');
const dbArg = dbFlagIndex >= 0 ? args[dbFlagIndex + 1] : undefined;

const here = fileURLToPath(new URL('.', import.meta.url));
// Compiled location is dist/scripts/: schema lives under dist/src, while the
// database and seed fixture live at the project root.
const defaultDb = resolve(here, '..', '..', 'data', 'app.db');
const dbFile = dbArg ? resolve(dbArg) : defaultDb;
const schemaPath = resolve(here, '..', 'src', 'state', 'schema.sql');
const seedPath = resolve(here, '..', '..', 'data', 'seed.json');

const handle = openDatabase(dbFile);
try {
  applySchema(handle.db, readFileSync(schemaPath, 'utf8'));
  if (reset) {
    handle.db.exec('DELETE FROM representations; DELETE FROM resources;');
  }
  const result = seedDatabase(handle.db, seedPath);
  process.stdout.write(`seeded ${result.resources} resource(s), ${result.representations} representation(s) into ${dbFile}\n`);
} finally {
  handle.close();
}
