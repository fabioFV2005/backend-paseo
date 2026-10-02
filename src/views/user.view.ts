import type { User } from '../models/user.model.js';
import type { UserRole, UserStatus } from '../types/user.types.js';

/** Fields the API is allowed to expose about a user. */
export interface UserResponse {
  id: number;
  fullName: string;
  email: string | null;
  phone: string | null;
  role: UserRole;
  status: UserStatus;
  avatarUrl: string | null;
  personalCode: string;
  referralCode: string;
  createdAt: Date;
}

/**
 * Serializes a User model for API responses.
 * NEVER expose: passwordHash, lastLoginAt, updatedAt, deletedAt.
 * If a field is not listed here, the client never sees it.
 */
export function toUserResponse(user: User): UserResponse {
  return {
    id: user.id,
    fullName: user.fullName,
    email: user.email,
    phone: user.phone,
    role: user.role,
    status: user.status,
    avatarUrl: user.avatarUrl,
    personalCode: user.personalCode,
    referralCode: user.referralCode,
    createdAt: user.createdAt,
  };
}
