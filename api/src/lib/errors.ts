import { FastifyReply } from 'fastify';

export type ErrorCode =
  | 'VALIDATION_ERROR'
  | 'NOT_FOUND'
  | 'UNAUTHORIZED'
  | 'FORBIDDEN'
  | 'RATE_LIMITED'
  | 'CONFLICT'
  | 'GONE' // it was there once and is no longer kept
  | 'SERVER_ERROR'
  | 'UNAVAILABLE'; // a service the API depends on is not set up or did not answer

const STATUS_MAP: Record<ErrorCode, number> = {
  VALIDATION_ERROR: 400,
  UNAUTHORIZED: 401,
  FORBIDDEN: 403,
  NOT_FOUND: 404,
  CONFLICT: 409,
  GONE: 410,
  RATE_LIMITED: 429,
  SERVER_ERROR: 500,
  UNAVAILABLE: 503,
};

export function sendError(reply: FastifyReply, code: ErrorCode, message: string, extra?: Record<string, unknown>) {
  return reply.status(STATUS_MAP[code]).send({ error: { code, message, ...extra } });
}

/** Thrown inside transactions; the route turns it into sendError. */
export class AppError extends Error {
  code: ErrorCode;
  extra?: Record<string, unknown>; // more fields for the error body, as sendError's own
  constructor(code: ErrorCode, message: string, extra?: Record<string, unknown>) {
    super(message);
    this.code = code;
    this.extra = extra;
  }
}

export function isAppError(err: unknown): err is AppError {
  return err instanceof AppError;
}

/** Prisma unique-constraint violation. */
export function isUniqueViolation(err: unknown): boolean {
  return !!err && typeof err === 'object' && 'code' in err && (err as { code: unknown }).code === 'P2002';
}

/**
 * A foreign key that points at nothing: the row it names was deleted under the write.
 * Prisma says P2003 for its own queries; a raw query's comes back as the Postgres
 * SQLSTATE 23503, on the error or one level down.
 */
export function isForeignKeyViolation(err: unknown): boolean {
  if (!err || typeof err !== 'object') return false;
  const { code, meta, cause } = err as { code?: unknown; meta?: { code?: unknown; driverAdapterError?: { cause?: { originalCode?: unknown; kind?: unknown } } }; cause?: unknown };
  if (code === 'P2003' || code === '23503') return true;
  if (meta?.code === '23503') return true;
  const adapter = meta?.driverAdapterError?.cause;
  if (adapter?.originalCode === '23503' || adapter?.kind === 'ForeignKeyConstraintViolation') return true;
  return !!cause && cause !== err && isForeignKeyViolation(cause);
}
