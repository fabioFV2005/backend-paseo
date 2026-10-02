import type { NextFunction, Request, Response } from 'express';
import { validationResult } from 'express-validator';

/**
 * Runs right after a validator chain in the route definition.
 * If any rule failed, it responds 400 with all messages joined
 * in the uniform error format: { "error": "msg1. msg2" }.
 */
export function validate(req: Request, res: Response, next: NextFunction): void {
  const result = validationResult(req);
  if (!result.isEmpty()) {
    const message = result
      .array()
      .map((error) => error.msg)
      .join('. ');
    res.status(400).json({ error: message });
    return;
  }
  next();
}
