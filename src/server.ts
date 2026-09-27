/**
 * Process entry point: loads the independent configuration file, builds
 * the app and starts listening.
 */

import { loadConfig } from './state/configLoader.js';
import { buildApp } from './app.js';
import { version } from './version.js';

async function main(): Promise<void> {
  const configPath = process.env.NEGOTIATION_CONFIG ?? 'config/negotiation.config.json';
  const config = await loadConfig(configPath);
  const { app, repo } = await buildApp(config);

  console.log(`[boot] seeded=${config.seedFixtures} database=${config.databasePath}`);

  const shutdown = async (): Promise<void> => {
    await app.close();
    repo.close();
    process.exit(0);
  };
  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);

  await app.listen({ port: config.port, host: '127.0.0.1' });
  console.log(
    `[boot] opp408-negotiation-service v${version} (config ${configPath}) listening on http://127.0.0.1:${config.port}`,
  );
  console.log(
    `[boot] policies: invalidEntry=${config.invalidEntryPolicy} duplicate=${config.duplicatePolicy} ` +
      `languageFallback=${config.languageFallback.enabled} (penalty=${config.languageFallback.penaltyPerStrippedSubtag})`,
  );
}

main().catch((error: unknown) => {
  console.error('[boot] fatal startup error:', error);
  process.exit(1);
});
