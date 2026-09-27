/**
 * HTTP routes — the diagnostics/interface layer.
 *
 * It stays thin: parse nothing itself, decide nothing itself. Every
 * decision comes from prepareNegotiation(); this module only maps it to
 * HTTP: status, Content-Type, Content-Language, Vary and an error body
 * carrying the explicit failure category.
 */

import type { FastifyInstance, FastifyPluginAsync } from 'fastify';
import type { ResourceRepository, StoredResource } from '../state/repository.js';
import type { TraceStore } from '../state/traceStore.js';
import type { RunLogger } from '../observability/runLogger.js';
import type { NegotiationOptions } from './negotiationService.js';
import { prepareNegotiation } from './negotiationService.js';
import { version } from '../version.js';

export interface RouteDeps {
  repo: ResourceRepository;
  traces: TraceStore;
  negotiation: NegotiationOptions;
  beginRun: () => RunLogger;
}

function fullContentType(resource: StoredResource, representationId: string): string {
  const rep = resource.representations.find((candidate) => candidate.id === representationId);
  if (!rep) return 'application/octet-stream';
  const params = Object.entries(rep.params)
    .map(([name, value]) => `; ${name}=${value}`)
    .join('');
  return `${rep.type}/${rep.subtype}${params}`;
}

export const routes: FastifyPluginAsync<RouteDeps> = async (app: FastifyInstance, deps: RouteDeps) => {
  app.get('/healthz', async () => ({ status: 'ok', version }));

  app.get('/resources', async () => ({
    resources: deps.repo.listPaths().map((path) => {
      const resource = deps.repo.getByPath(path);
      return {
        path: resource.path,
        title: resource.title,
        representations: resource.representations.map((rep) => ({
          id: rep.id,
          mediaType: `${rep.type}/${rep.subtype}`,
          params: rep.params,
          language: rep.language,
        })),
      };
    }),
  }));

  // Diagnostics: recent negotiation runs, newest first (?runId= filters).
  app.get<{ Querystring: { runId?: string } }>('/diagnostics', async (request) => {
    const runId = typeof request.query.runId === 'string' ? request.query.runId : undefined;
    return {
      version,
      count: deps.traces.size,
      runs: deps.traces.list(runId).map((record) => ({
        runId: record.runId,
        timestamp: record.timestamp,
        method: record.method,
        url: record.url,
        resourcePath: record.resourcePath,
        requestHeaders: record.requestHeaders,
        status: record.status,
        failureCategory: record.failureCategory,
        selectedId: record.selectedId,
        vary: record.vary,
        influencedHeaders: record.result.influencedHeaders,
        scores: record.result.scores,
        counterfactual: record.result.counterfactual,
        warnings: record.result.warnings,
        traces: record.result.traces,
      })),
    };
  });

  app.get('/', async () => ({
    service: 'opp408-negotiation-service',
    version,
    resources: deps.repo.listPaths(),
    hint: 'GET a resource with Accept / Accept-Language; inspect /diagnostics?runId=<X-Run-Id>',
  }));

  app.get<{ Params: { name: string } }>('/:name', async (request, reply) => {
    const logger = deps.beginRun();
    const resourcePath = `/${request.params.name}`;
    const accept = request.headers.accept;
    const acceptLanguage = request.headers['accept-language'];

    logger.emit('request', 'received resource request', {
      method: request.method,
      url: request.url,
      resourcePath,
      accept: accept ?? null,
      acceptLanguage: acceptLanguage ?? null,
    });

    let resource: StoredResource;
    try {
      resource = deps.repo.getByPath(resourcePath);
    } catch {
      logger.emit('error', 'unknown resource path', { resourcePath });
      reply.status(404).header('X-Run-Id', logger.runId);
      return {
        error: {
          category: 'NOT_FOUND',
          message: `No resource at ${resourcePath}. See /resources.`,
        },
      };
    }

    const { result } = prepareNegotiation(resource.representations, { accept, acceptLanguage }, deps.negotiation);

    logger.emit('parse-media', 'Accept entries parsed', {
      warnings: result.warnings.filter((warning) =>
        ['INVALID_Q', 'MALFORMED_ENTRY', 'MALFORMED_PARAMETER', 'DUPLICATE_ENTRY', 'UNKNOWN_PARAMETER'].includes(
          warning.code,
        ),
      ),
    });
    for (const trace of result.traces) {
      logger.emit('score', `candidate ${trace.candidateId} evaluated`, {
        run: trace.candidateId,
        feasible: trace.feasible,
        steps: trace.steps,
      });
    }
    logger.emit('decide', 'negotiation decision', {
      ok: result.ok,
      status: result.httpStatus,
      failureCategory: result.failureCategory,
      failureMessage: result.failureMessage,
      selectedId: result.selected?.id ?? null,
      vary: result.vary,
      influencedHeaders: result.influencedHeaders,
      counterfactual: result.counterfactual,
    });
    if (!result.ok) {
      logger.emit('error', 'negotiation failed', {
        status: result.httpStatus,
        failureCategory: result.failureCategory,
      });
    }

    deps.traces.add({
      runId: logger.runId,
      timestamp: logger.startedAt,
      method: request.method,
      url: request.url,
      resourcePath,
      requestHeaders: { accept, acceptLanguage },
      status: result.httpStatus,
      failureCategory: result.failureCategory,
      selectedId: result.selected?.id ?? null,
      vary: result.vary,
      result,
    });

    // Vary contains exactly the headers whose counterfactual changed the
    // decision; when neither header matters we send nothing (never a
    // wildcard) so cache keys stay truthful.
    if (result.vary.length > 0) reply.header('Vary', result.vary.join(', '));
    reply.header('X-Run-Id', logger.runId);

    if (!result.ok || result.selected === null) {
      logger.emit('respond', 'sending explicit failure response', {
        status: result.httpStatus,
        failureCategory: result.failureCategory,
      });
      reply.status(result.httpStatus);
      return {
        error: {
          category: result.failureCategory,
          status: result.httpStatus,
          message: result.failureMessage,
        },
        vary: result.vary,
        influencedHeaders: result.influencedHeaders,
        warnings: result.warnings,
        candidateEvaluations: result.traces.map((trace) => ({
          candidateId: trace.candidateId,
          feasible: trace.feasible,
          media: { matched: trace.media.matched, q: trace.media.q, forbiddenByZero: trace.media.forbiddenByZero },
          language: {
            matched: trace.languageResult.matched,
            q: trace.languageResult.q,
            kind: trace.languageResult.kind,
            forbiddenByZero: trace.languageResult.forbiddenByZero,
          },
          steps: trace.steps,
        })),
        runId: logger.runId,
      };
    }

    const representation = resource.representations.find((candidate) => candidate.id === result.selected!.id)!;
    logger.emit('respond', 'sending selected representation', {
      selectedId: representation.id,
      contentType: fullContentType(resource, representation.id),
      contentLanguage: representation.language,
    });
    reply
      .status(200)
      .header('Content-Type', fullContentType(resource, representation.id))
      .header('Content-Language', representation.language)
      .header('X-Selected-Representation', representation.id);
    return representation.body;
  });
};
