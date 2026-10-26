import { FastifyReply } from 'fastify';

export type ErrorCode =
  | 'VALIDATION_ERROR'
  | 'NOT_FOUND'
  | 'UNAUTHORIZED'
  | 'FORBIDDEN'
  | 'PENDING_APPROVAL' // signed in, an admin has not approved the account yet
  | 'ACCESS_DENIED' // signed in, an admin denied the account
  | 'RATE_LIMITED'
  | 'CONFLICT'
  | 'GONE' // it was there once and is no longer kept
  | 'SERVER_ERROR';

const STATUS_MAP: Record<ErrorCode, number> = {
  VALIDATION_ERROR: 400,
  UNAUTHORIZED: 401,
  FORBIDDEN: 403,
  PENDING_APPROVAL: 403,
  ACCESS_DENIED: 403,
  NOT_FOUND: 404,
  CONFLICT: 409,
  GONE: 410,
  RATE_LIMITED: 429,
  SERVER_ERROR: 500,
};

export function sendError(reply: FastifyReply, code: ErrorCode, message: string, extra?: Record<string, unknown>) {
  return reply.status(STATUS_MAP[code]).send({ error: { code, message, ...extra } });
}

/** Thrown inside transactions; the route turns it into sendError. */
export class AppError extends Error {
  code: ErrorCode;
  constructor(code: ErrorCode, message: string) {
    super(message);
    this.code = code;
  }
}

export function isAppError(err: unknown): err is AppError {
  return err instanceof AppError;
}

/** Prisma unique-constraint violation. */
export function isUniqueViolation(err: unknown): boolean {
  return !!err && typeof err === 'object' && 'code' in err && (err as { code: unknown }).code === 'P2002';
}
