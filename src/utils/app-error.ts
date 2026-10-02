/**
 * Operational error with an HTTP status code.
 * Thrown from any layer, it is formatted by the global error middleware
 * into the uniform response shape: { "error": "message" }.
 *
 * The constructor is private on purpose: use the named factories so
 * status codes stay consistent across the whole API.
 */
export class AppError extends Error {
  public readonly statusCode: number;

  private constructor(statusCode: number, message: string) {
    super(message);
    this.name = 'AppError';
    this.statusCode = statusCode;
  }

  static badRequest(message: string): AppError {
    return new AppError(400, message);
  }

  static unauthorized(message = 'Authentication required'): AppError {
    return new AppError(401, message);
  }

  static forbidden(message = 'Access denied'): AppError {
    return new AppError(403, message);
  }

  static notFound(message = 'Resource not found'): AppError {
    return new AppError(404, message);
  }

  static conflict(message: string): AppError {
    return new AppError(409, message);
  }
}
