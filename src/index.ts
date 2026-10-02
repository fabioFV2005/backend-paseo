import { connectDatabase } from './config/db.js';
import { env } from './config/env.js';
import { syncModels } from './models/index.js';
import { createServer } from './server.js';

/**
 * Entry point. Responsibilities:
 * 1. Connect to the database.
 * 2. Start the HTTP server.
 * If any step fails, the process exits instead of running half-configured.
 */
async function bootstrap(): Promise<void> {
  await connectDatabase();

  // Development convenience: creates/updates tables from model definitions.
  // Phase 10 introduces proper migrations for production use.
  if (env.isDevelopment) {
    await syncModels();
  }

  const app = createServer();
  app.listen(env.port, () => {
    console.log(`[server] API listening on http://localhost:${env.port}`);
  });
}

bootstrap().catch((error: unknown) => {
  console.error('[server] Fatal error during startup:', error);
  process.exit(1);
});
