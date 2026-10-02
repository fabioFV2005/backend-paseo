import { sequelize } from '../config/db.js';
import { User } from './user.model.js';

/**
 * Model registry. Import models from here (never from their files directly)
 * so associations are always initialized before use.
 */

// Self-reference: a customer may have been referred by another user.
User.belongsTo(User, { as: 'referrer', foreignKey: 'referredBy' });

export { sequelize, User };

/**
 * Development helper: syncs model definitions with the database schema.
 * Called from src/index.ts only when NODE_ENV=development.
 */
export async function syncModels(): Promise<void> {
  await sequelize.sync();
  console.log('[db] Models synchronized');
}
