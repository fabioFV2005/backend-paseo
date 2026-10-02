import jwt, { type SignOptions } from 'jsonwebtoken';
import { env } from '../config/env.js';
import type { UserRole } from '../types/user.types.js';

/** Payload stored inside every signed token. Keep it small: id + role only. */
export interface AuthTokenPayload {
  id: number;
  role: UserRole;
}

export function signAuthToken(payload: AuthTokenPayload): string {
  // expiresIn accepts the same formats as the `ms` package: '1d', '12h', 3600...
  return jwt.sign(payload, env.jwt.secret, {
    expiresIn: env.jwt.expiresIn as SignOptions['expiresIn'],
  });
}

export function verifyAuthToken(token: string): AuthTokenPayload {
  const decoded = jwt.verify(token, env.jwt.secret);
  if (typeof decoded === 'string' || typeof decoded.id !== 'number' || typeof decoded.role !== 'string') {
    throw new Error('Invalid token payload');
  }
  return { id: decoded.id, role: decoded.role as UserRole };
}
