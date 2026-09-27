/**
 * Process entry point: load config, open/seed SQLite, build the Fastify app.
 */
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadConfig } from '../config.js';
import { applySchema, isSeeded, openDatabase, seedDatabase, ResourceRepository } from '../state/repository.js';
import { readFileSync } from 'node:fs';
import { buildApp } from './app.js';

async function main(): Promise<void> {
  const config = loadConfig();
  const here = fileURLToPath(new URL('.', import.meta.url));
  // Works for both src/http (strip-types) and dist/http (compiled).
  const schemaPath = resolve(here, '..', 'state', 'schema.sql');
  const seedPath = resolve(here, '..', '..', 'data', 'seed.json');

  const handle = openDatabase(config.database.file);
  const schemaSql = readFileSync(schemaPath, 'utf8');
  applySchema(handle.db, schemaSql);
  if (config.database.seedOnBoot && !isSeeded(handle.db)) {
    const result = seedDatabase(handle.db, seedPath);
    process.stdout.write(`seeded ${result.resources} resource(s), ${result.representations} representation(s)\n`);
  }

  const app = await buildApp({ config, repository: new ResourceRepository(handle.db) });

  const signals: NodeJS.Signals[] = ['SIGINT', 'SIGTERM'];
  for (const signal of signals) {
    process.on(signal, () => {
      app.close().finally(() => {
        handle.close();
        process.exit(0);
      });
    });
  }

  await app.listen({ host: config.server.host, port: config.server.port });
  app.log.info(
    { host: config.server.host, port: config.server.port, db: config.database.file, version: process.version },
    'variant resource service listening',
  );
}

main().catch((err: unknown) => {
  process.stderr.write(`fatal: ${err instanceof Error ? err.stack ?? err.message : String(err)}\n`);
  process.exit(1);
});
