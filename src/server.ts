import express, { type Express } from 'express';
import { errorHandler, notFoundHandler } from './middlewares/error.middleware.js';
import { apiRouter } from './routes/index.js';

/**
 * Builds the Express application: middlewares first, routes second,
 * error handlers last. It knows nothing about ports or databases —
 * that is src/index.ts's job.
 */
export function createServer(): Express {
  const app = express();

  app.disable('x-powered-by');
  app.use(express.json());

  // Liveness probe, useful for Docker and uptime monitors.
  app.get('/health', (_req, res) => {
    res.json({ status: 'ok' });
  });

  app.use('/api', apiRouter);

  // Catch-alls, always at the end and in this order.
  app.use(notFoundHandler);
  app.use(errorHandler);

  return app;
}
