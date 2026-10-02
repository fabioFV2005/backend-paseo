import { randomUUID } from 'node:crypto';
import {
  DataTypes,
  Model,
  type CreationOptional,
  type InferAttributes,
  type InferCreationAttributes,
} from 'sequelize';
import { sequelize } from '../config/db.js';
import { USER_ROLES, USER_STATUSES, type UserRole, type UserStatus } from '../types/user.types.js';

/** Mirrors the SQL default: first 10 chars of a UUID, without dashes, uppercased. */
function generatePersonalCode(): string {
  return randomUUID().replaceAll('-', '').slice(0, 10).toUpperCase();
}

/** Mirrors the SQL default: 8 chars starting at position 11 of a UUID. */
function generateReferralCode(): string {
  return randomUUID().replaceAll('-', '').slice(10, 18).toUpperCase();
}

/**
 * Users table. Maps 1:1 to the original SQL design:
 * - BIGINT identity primary key.
 * - email / phone optional but at least one is required (see validate below).
 * - personal_code (QR content) and referral_code auto-generated.
 * - Soft delete via deleted_at (global paranoid setting in config/db.ts).
 */
export class User extends Model<InferAttributes<User>, InferCreationAttributes<User>> {
  declare id: CreationOptional<number>;
  declare fullName: string;
  declare email: CreationOptional<string | null>;
  declare phone: CreationOptional<string | null>;
  declare passwordHash: string;
  declare role: CreationOptional<UserRole>;
  declare status: CreationOptional<UserStatus>;
  declare avatarUrl: CreationOptional<string | null>;
  declare personalCode: CreationOptional<string>;
  declare referralCode: CreationOptional<string>;
  declare referredBy: CreationOptional<number | null>;
  declare lastLoginAt: CreationOptional<Date | null>;
  declare createdAt: CreationOptional<Date>;
  declare updatedAt: CreationOptional<Date>;
  declare deletedAt: CreationOptional<Date | null>;
}

User.init(
  {
    id: { type: DataTypes.BIGINT, autoIncrement: true, primaryKey: true },
    fullName: { type: DataTypes.TEXT, allowNull: false },
    email: { type: DataTypes.TEXT, allowNull: true, unique: true, validate: { isEmail: true } },
    phone: { type: DataTypes.TEXT, allowNull: true, unique: true },
    passwordHash: { type: DataTypes.TEXT, allowNull: false },
    role: { type: DataTypes.ENUM(...USER_ROLES), allowNull: false, defaultValue: 'customer' },
    status: { type: DataTypes.ENUM(...USER_STATUSES), allowNull: false, defaultValue: 'active' },
    avatarUrl: { type: DataTypes.TEXT, allowNull: true },
    personalCode: { type: DataTypes.TEXT, allowNull: false, unique: true, defaultValue: generatePersonalCode },
    referralCode: { type: DataTypes.TEXT, allowNull: false, unique: true, defaultValue: generateReferralCode },
    referredBy: { type: DataTypes.BIGINT, allowNull: true },
    lastLoginAt: { type: DataTypes.DATE, allowNull: true },
    // Declared explicitly so TypeScript requires them in the class above.
    // Sequelize manages their values automatically (timestamps + paranoid).
    createdAt: { type: DataTypes.DATE, allowNull: false },
    updatedAt: { type: DataTypes.DATE, allowNull: false },
    deletedAt: { type: DataTypes.DATE, allowNull: true },
  },
  {
    sequelize,
    tableName: 'users',
    validate: {
      // Same rule as the SQL CHECK constraint: email or phone is required.
      atLeastOneContact(this: User): void {
        if (!this.email && !this.phone) {
          throw new Error('Either email or phone is required');
        }
      },
    },
  },
);
