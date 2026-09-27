/**
 * Application composition: builds a Fastify instance with all layers wired.
 * Shared by server.ts (real process) and the E2E tests (in-memory SQLite).
 */

import Fastify, { type FastifyInstance } from 'fastify';
import { ResourceRepository } from './state/repository.js';
import { TraceStore } from './state/traceStore.js';
import { routes } from './http/routes.js';
import { RunLogger } from './observability/runLogger.js';
import type { ServiceConfig } from './state/configLoader.js';

export interface BuiltApp {
  app: FastifyInstance;
  repo: ResourceRepository;
  traces: TraceStore;
}

export async function buildApp(
  config: ServiceConfig,
  loggerSink?: (line: string) => void,
): Promise<BuiltApp> {
  const repo = ResourceRepository.open(config.databasePath);
  if (config.seedFixtures && !repo.isSeeded()) repo.seed();

  const traces = new TraceStore(config.traceBufferSize);
  const app = Fastify({ logger: false });

  await app.register(routes, {
    repo,
    traces,
    negotiation: {
      policy: {
        invalidEntryPolicy: config.invalidEntryPolicy,
        duplicatePolicy: config.duplicatePolicy,
      },
      fallback: {
        enabled: config.languageFallback.enabled,
        penaltyPerStrippedSubtag: config.languageFallback.penaltyPerStrippedSubtag,
      },
      maxAcceptEntries: config.maxAcceptEntries,
    },
    beginRun: () => RunLogger.begin(loggerSink === undefined ? undefined : { sink: loggerSink }),
  });

  return { app, repo, traces };
}
