import type { NextFunction, Request, Response } from 'express';

type AsyncRouteHandler = (req: Request, res: Response, next: NextFunction) => Promise<unknown>;

/**
 * Wraps an async route handler so rejected promises are forwarded to the
 * global error middleware instead of crashing the process.
 * (Express 4 does not catch errors thrown inside async handlers natively.)
 */
export function asyncHandler(handler: AsyncRouteHandler) {
  return (req: Request, res: Response, next: NextFunction): void => {
    handler(req, res, next).catch(next);
  };
}
