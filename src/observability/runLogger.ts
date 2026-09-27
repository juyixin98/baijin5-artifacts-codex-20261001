/**
 * Structured run logger.
 *
 * Every line is one JSON object on stdout and carries:
 *   - service/version fields (environment + module version)
 *   - runId:    correlates every line of one negotiation
 *   - phase:    pipeline stage (parse-media / parse-language / score / decide)
 *   - step:     monotonic per-run sequence number (progress + reasoning)
 *   - detail:   inputs, intermediate values and the judgement basis
 *
 * Failures are logged at "error" level with their failureCategory —
 * unknown/exceptional states are never folded into a success line.
 */

import { randomUUID } from 'node:crypto';
import { version } from '../version.js';

export type LogPhase =
  | 'request'
  | 'parse-media'
  | 'parse-language'
  | 'score'
  | 'decide'
  | 'respond'
  | 'error';

export interface RunLoggerOptions {
  nodeVersion: string;
  platform: string;
  sink: (line: string) => void;
}

export class RunLogger {
  private counter = 0;
  readonly startedAt = new Date().toISOString();

  constructor(
    readonly runId: string,
    private readonly options: RunLoggerOptions,
  ) {}

  static begin(options?: Partial<RunLoggerOptions>): RunLogger {
    const runId = randomUUID();
    const logger = new RunLogger(runId, {
      nodeVersion: process.version,
      platform: process.platform,
      sink: (line) => process.stdout.write(`${line}\n`),
      ...options,
    });
    logger.emit('request', 'run-started', { service: 'opp408-negotiation-service', version });
    return logger;
  }

  emit(phase: LogPhase, basis: string, detail: Record<string, unknown>): void {
    this.counter++;
    const record = {
      time: new Date().toISOString(),
      level: phase === 'error' ? 'error' : 'info',
      service: 'opp408-negotiation-service',
      version,
      node: this.options.nodeVersion,
      platform: this.options.platform,
      runId: this.runId,
      step: this.counter,
      phase,
      basis,
      ...detail,
    };
    this.options.sink(JSON.stringify(record));
  }
}
