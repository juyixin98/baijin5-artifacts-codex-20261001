/**
 * Fastify composition root and routes. The negotiation core is pure and the
 * repository is injected, so this module only wires HTTP to domain logic.
 */
import Fastify, { type FastifyInstance } from 'fastify';
import { negotiate } from '../core/negotiator.js';
import type { AppConfig } from '../config.js';
import type { ResourceRepository } from '../state/repository.js';
import type { NegotiationTrace } from '../core/types.js';
import { NegotiationError } from '../contract/errors.js';
import { negotiationErrorView, responseContentType, varyHeader } from './present.js';
import { logTrace } from './negotiation-log.js';

export interface AppDeps {
  readonly config: AppConfig;
  readonly repository: ResourceRepository;
}

function runNegotiation(repo: ResourceRepository, config: AppConfig, resourceId: string, headers: {
  accept?: string | undefined;
  acceptLanguage?: string | undefined;
}): { resource: NonNullable<ReturnType<ResourceRepository['getResource']>>; trace: NegotiationTrace } | { missing: true } {
  const resource = repo.getResource(resourceId);
  if (resource === null) return { missing: true };
  const trace = negotiate({
    resource,
    acceptHeader: headers.accept ?? null,
    acceptLanguageHeader: headers.acceptLanguage ?? null,
    config: config.negotiation,
  });
  return { resource, trace };
}

export async function buildApp(deps: AppDeps): Promise<FastifyInstance> {
  const { config, repository } = deps;
  const app = Fastify({
    logger: { level: config.logging.level },
    forceCloseConnections: true,
  });

  app.get('/healthz', async () => ({ status: 'ok' }));

  // List resources and the variants actually offered.
  app.get(config.resources.basePath, async () => ({
    resources: repository.listResourceIds().map((id) => {
      const resource = repository.getResource(id)!;
      return {
        id: resource.id,
        name: resource.name,
        variants: resource.representations.map((r) => ({
          id: r.id,
          mediaType: `${r.mediaType.type}/${r.mediaType.subtype}`,
          mediaParams: Object.fromEntries(r.mediaType.parameters),
          language: r.language,
        })),
      };
    }),
  }));

  // Diagnostic: explain the decision without returning the representation
  // body. Mirrors the exact status the resource endpoint would produce; a
  // failed negotiation is reported with its real 400/406 status, never 200.
  app.get(`${config.resources.basePath}/:id/trace`, async (request, reply) => {
    const { id } = request.params as { id: string };
    const result = runNegotiation(repository, config, id, {
      accept: request.headers.accept,
      acceptLanguage: request.headers['accept-language'],
    });
    if ('missing' in result) {
      return reply.code(404).send({ error: 'NOT_FOUND', status: 404, message: `Unknown resource "${id}"` });
    }
    logTrace(request.log, result.trace);
    if (result.trace.failure !== null) {
      const error = new NegotiationError(
        result.trace.failure.code as NegotiationError['code'],
        result.trace.failure.stage as NegotiationError['stage'],
        result.trace.failure.message,
      );
      const view = negotiationErrorView(error, result.trace.runId);
      return reply.code(view.status).header('vary', varyHeader(result.trace)).send({ ...view.body, trace: result.trace });
    }
    return reply.code(200).header('vary', varyHeader(result.trace)).send({ trace: result.trace });
  });

  // The negotiated resource itself.
  app.get(`${config.resources.basePath}/:id`, async (request, reply) => {
    const { id } = request.params as { id: string };
    const result = runNegotiation(repository, config, id, {
      accept: request.headers.accept,
      acceptLanguage: request.headers['accept-language'],
    });
    if ('missing' in result) {
      request.log.warn({ resourceId: id }, 'unknown resource requested');
      return reply.code(404).send({ error: 'NOT_FOUND', status: 404, message: `Unknown resource "${id}"` });
    }

    logTrace(request.log, result.trace);
    reply.header('vary', varyHeader(result.trace));
    reply.header('x-negotiation-run-id', result.trace.runId);

    if (result.trace.failure !== null) {
      const failure = result.trace.failure;
      const error = new NegotiationError(
        failure.code as NegotiationError['code'],
        failure.stage as NegotiationError['stage'],
        failure.message,
      );
      const view = negotiationErrorView(error, result.trace.runId);
      const offered = repository.getResource(id)?.representations.map((r) => ({
        mediaType: `${r.mediaType.type}/${r.mediaType.subtype}`,
        language: r.language,
      })) ?? [];
      return reply.code(view.status).send({ ...view.body, offered });
    }

    const winnerId = result.trace.winner!.representationId;
    const rep = result.resource.representations.find((r) => r.id === winnerId)!;
    return reply
      .code(200)
      .header('content-type', responseContentType(rep))
      .header('content-language', rep.language)
      .header('x-selected-representation', rep.id)
      .send(rep.body);
  });

  // 404 for unknown routes, kept distinct from negotiation failures.
  app.setNotFoundHandler((request, reply) => {
    void reply.code(404).send({ error: 'NOT_FOUND', status: 404, message: `No route for ${request.method} ${request.url}` });
  });

  // Never map an unexpected error to a success envelope.
  app.setErrorHandler((error, request, reply) => {
    request.log.error({ err: error, runScope: 'http' }, 'unhandled error');
    if (error instanceof NegotiationError) {
      const view = negotiationErrorView(error);
      void reply.code(view.status).send(view.body);
      return;
    }
    void reply.code(500).send({ error: 'INTERNAL_ERROR', status: 500, message: 'Internal server error' });
  });

  return app;
}
