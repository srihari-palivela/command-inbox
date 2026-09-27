/** Application errors map 1:1 onto RFC 9457 problem details. */
export class AppError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly detail?: string,
  ) {
    super(message);
    this.name = 'AppError';
  }
}

export const badRequest = (code: string, message: string, detail?: string) =>
  new AppError(400, code, message, detail);
export const unauthorized = (message = 'Sign in to continue') =>
  new AppError(401, 'unauthenticated', message);
export const forbidden = (message: string, code = 'forbidden') => new AppError(403, code, message);
export const notFound = (what: string) => new AppError(404, 'not_found', `${what} not found`);
export const conflict = (code: string, message: string, detail?: string) =>
  new AppError(409, code, message, detail);
export const unprocessable = (code: string, message: string, detail?: string) =>
  new AppError(422, code, message, detail);
