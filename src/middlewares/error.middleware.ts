import type { NextFunction, Request, Response } from 'express';
import { AppError } from '../utils/app-error.js';

/** Handles requests that reach the server but match no route. */
export function notFoundHandler(req: Request, res: Response): void {
  res.status(404).json({ error: `Route not found: ${req.method} ${req.originalUrl}` });
}

/**
 * Global error handler. Must declare exactly 4 parameters so Express
 * recognizes it as an error-handling middleware.
 *
 * Output is always the uniform shape: { "error": "message" }.
 */
export function errorHandler(err: unknown, _req: Request, res: Response, _next: NextFunction): void {
  // Operational errors thrown by services/middlewares (AppError factories).
  if (err instanceof AppError) {
    res.status(err.statusCode).json({ error: err.message });
    return;
  }

  // express.json() throws a SyntaxError when the request body is not valid JSON.
  if (err instanceof SyntaxError) {
    res.status(400).json({ error: 'Invalid JSON in request body' });
    return;
  }

  // Unknown errors are logged but never leaked to the client.
  console.error('[error] Unexpected error:', err);
  res.status(500).json({ error: 'Internal server error' });
}
