/**
 * Configuration loading: defaults file merged with an optional file pointed
 * to by RESOURCE_SERVICE_CONFIG, then a small allow-list of environment
 * overrides. JSON is validated up front; an invalid file fails fast.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import type { NegotiationConfig } from './core/negotiator.js';

export interface AppConfig {
  readonly server: { readonly host: string; readonly port: number; readonly requestTimeoutMs: number };
  readonly database: { readonly file: string; readonly seedOnBoot: boolean };
  readonly negotiation: NegotiationConfig;
  readonly logging: { readonly level: string };
  readonly resources: { readonly basePath: string };
}

function fail(message: string): never {
  throw new Error(`Invalid configuration: ${message}`);
}

function validate(raw: Record<string, unknown>): AppConfig {
  const server = raw.server as Record<string, unknown> | undefined;
  const database = raw.database as Record<string, unknown> | undefined;
  const negotiation = raw.negotiation as Record<string, unknown> | undefined;
  const logging = raw.logging as Record<string, unknown> | undefined;
  const resources = raw.resources as Record<string, unknown> | undefined;

  if (!server || typeof server.host !== 'string' || !Number.isInteger(server.port) || (server.port as number) <= 0) {
    fail('server.host must be a string and server.port a positive integer');
  }
  if (typeof server.requestTimeoutMs !== 'number' || server.requestTimeoutMs <= 0) fail('server.requestTimeoutMs must be > 0');
  if (!database || typeof database.file !== 'string' || database.file.trim() === '') fail('database.file must be a non-empty string');
  if (typeof database.seedOnBoot !== 'boolean') fail('database.seedOnBoot must be a boolean');
  if (!negotiation) fail('missing negotiation section');

  const absentAccept = negotiation.absentAccept;
  const absentLang = negotiation.absentAcceptLanguage;
  if (absentAccept !== 'wildcard' && absentAccept !== 'default') fail('negotiation.absentAccept must be "wildcard" or "default"');
  if (absentLang !== 'wildcard' && absentLang !== 'default') fail('negotiation.absentAcceptLanguage must be "wildcard" or "default"');
  if (negotiation.unknownParameters !== 'ignore' && negotiation.unknownParameters !== 'reject') {
    fail('negotiation.unknownParameters must be "ignore" or "reject"');
  }
  if (negotiation.languageFallback !== 'lookup' && negotiation.languageFallback !== 'filtering') {
    fail('negotiation.languageFallback must be "lookup" or "filtering"');
  }
  if (typeof negotiation.defaultMediaType !== 'string') fail('negotiation.defaultMediaType must be a string');
  if (typeof negotiation.defaultLanguage !== 'string') fail('negotiation.defaultLanguage must be a string');
  if (!logging || typeof logging.level !== 'string') fail('logging.level must be a string');
  if (!resources || typeof resources.basePath !== 'string' || !resources.basePath.startsWith('/')) {
    fail('resources.basePath must be a string starting with "/"');
  }

  return {
    server: { host: server.host, port: server.port as number, requestTimeoutMs: server.requestTimeoutMs as number },
    database: { file: database.file as string, seedOnBoot: database.seedOnBoot as boolean },
    negotiation: {
      absentAccept,
      absentAcceptLanguage: absentLang,
      unknownParameters: negotiation.unknownParameters,
      languageFallback: negotiation.languageFallback,
      defaultMediaType: negotiation.defaultMediaType as string,
      defaultLanguage: negotiation.defaultLanguage as string,
    },
    logging: { level: logging.level },
    resources: { basePath: resources.basePath },
  };
}

export function loadConfig(options: { env?: NodeJS.ProcessEnv; explicitPath?: string } = {}): AppConfig {
  const env = options.env ?? process.env;
  const here = new URL('.', import.meta.url).pathname;
  const defaultsPath = resolve(here, '..', 'config', 'default.json');
  const base = JSON.parse(readFileSync(defaultsPath, 'utf8')) as Record<string, unknown>;
  const merged: Record<string, unknown> = structuredClone(base);

  const overridePath = options.explicitPath ?? env.RESOURCE_SERVICE_CONFIG;
  if (overridePath) {
    const extra = JSON.parse(readFileSync(resolve(overridePath), 'utf8')) as Record<string, unknown>;
    for (const [section, value] of Object.entries(extra)) {
      merged[section] = typeof value === 'object' && value !== null && !Array.isArray(value)
        ? { ...(merged[section] as Record<string, unknown>), ...(value as Record<string, unknown>) }
        : value;
    }
  }

  const config = validate(merged);

  // Environment allow-list (highest precedence).
  const port = env.PORT ? Number(env.PORT) : undefined;
  if (port !== undefined) {
    if (!Number.isInteger(port) || port <= 0) fail('PORT must be a positive integer');
    (config as { server: { port: number } }).server.port = port;
  }
  if (env.HOST) (config as { server: { host: string } }).server.host = env.HOST;
  if (env.DB_FILE) (config as { database: { file: string } }).database.file = env.DB_FILE;

  return config;
}
