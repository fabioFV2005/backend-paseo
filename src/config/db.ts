import pg from 'pg';
import { Sequelize } from 'sequelize';
import { env } from './env.js';

// node-pg returns BIGINT columns as strings. Our ids fit safely in JS numbers
// (up to 2^53), so we parse them as numbers to keep the whole codebase simple.
pg.types.setTypeParser(pg.types.builtins.INT8, (value: string) => Number(value));

/**
 * Single Sequelize instance shared by every model.
 *
 * Global `define` defaults keep the database consistent:
 * - underscored: camelCode in JS, snake_case columns in PostgreSQL.
 * - timestamps: manages created_at / updated_at automatically.
 * - paranoid: soft delete via deleted_at (rows are never physically removed).
 */
export const sequelize = new Sequelize(env.db.name, env.db.user, env.db.password, {
  host: env.db.host,
  port: env.db.port,
  dialect: 'postgres',
  logging: env.isDevelopment ? (msg) => console.log(`[db] ${msg}`) : false,
  define: {
    underscored: true,
    timestamps: true,
    paranoid: true,
  },
});

/**
 * Verifies credentials and network access against PostgreSQL.
 * Called once from src/index.ts before the HTTP server starts listening.
 */
export async function connectDatabase(): Promise<void> {
  await sequelize.authenticate();
  console.log('[db] Connection established successfully');
}
