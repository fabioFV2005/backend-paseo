/**
 * Domain types shared across layers (models, services, views).
 * Kept out of the model file so any layer can import them without
 * pulling in Sequelize or creating circular dependencies.
 */

/** Roles a user can have within the marketplace. */
export const USER_ROLES = ['customer', 'store_owner', 'admin'] as const;
export type UserRole = (typeof USER_ROLES)[number];

/** Lifecycle states of a user account. */
export const USER_STATUSES = ['active', 'suspended'] as const;
export type UserStatus = (typeof USER_STATUSES)[number];
