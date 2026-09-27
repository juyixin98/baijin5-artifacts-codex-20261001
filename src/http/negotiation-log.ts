/**
 * Structured negotiation logging. Every negotiation emits one JSON line per
 * run carrying the run id, service version, raw inputs, parsed ranges,
 * per-candidate computation and the verdict, so a log line is enough to
 * reproduce the decision. Parse failures and negotiation failures are
 * logged at "warn"; successes at "info". A failure is never logged as
 * success.
 */
import type { NegotiationTrace } from '../core/types.js';

export interface LogSink {
  info(obj: Record<string, unknown>, msg: string): void;
  warn(obj: Record<string, unknown>, msg: string): void;
}

export function logTrace(sink: LogSink, trace: NegotiationTrace): void {
  const payload: Record<string, unknown> = {
    runId: trace.runId,
    serviceVersion: trace.serviceVersion,
    resourceId: trace.resourceId,
    startedAt: trace.startedAt,
    elapsedMs: Number(trace.elapsedMs.toFixed(4)),
    accept: trace.acceptHeaderRaw,
    acceptLanguage: trace.acceptLanguageHeaderRaw,
    vary: trace.vary,
    notices: trace.notices,
    steps: trace.steps,
    scores: trace.candidateScores.map((s) => ({
      representationId: s.representationId,
      mediaType: s.mediaType,
      language: s.language,
      mediaQuality: s.mediaQuality,
      languageQuality: s.languageQuality,
      combined: s.combined,
    })),
  };

  if (trace.failure !== null) {
    sink.warn(
      {
        ...payload,
        failureCode: trace.failure.code,
        failureStage: trace.failure.stage,
      },
      `negotiation ${trace.runId} FAILED ${trace.failure.code} @ ${trace.failure.stage}`,
    );
    return;
  }

  sink.info(
    {
      ...payload,
      winner: trace.winner,
    },
    `negotiation ${trace.runId} selected ${trace.winner?.representationId}`,
  );
}
