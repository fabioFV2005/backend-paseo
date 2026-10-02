import { connectDatabase, sequelize } from '../src/config/db.js';

/**
 * Manual verification script: npm run db:check
 * Confirms that the .env credentials can open a connection to PostgreSQL.
 */
try {
  await connectDatabase();
} catch (error) {
  console.error('[db] Connection failed:', error instanceof Error ? error.message : error);
  process.exitCode = 1;
} finally {
  await sequelize.close();
}
