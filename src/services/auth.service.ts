import { UniqueConstraintError } from 'sequelize';
import { User } from '../models/index.js';
import { AppError } from '../utils/app-error.js';
import { comparePassword, hashPassword } from '../utils/hash.js';
import { signAuthToken } from '../utils/jwt.js';

export interface RegisterInput {
  fullName: string;
  email?: string;
  phone?: string;
  password: string;
}

export interface LoginInput {
  email: string;
  password: string;
}

export interface AuthResult {
  token: string;
  user: User;
}

/**
 * Creates a new customer account and returns a signed token (auto-login).
 * Throws AppError.conflict (409) when email or phone is already taken.
 */
export async function registerUser(input: RegisterInput): Promise<AuthResult> {
  if (!input.email && !input.phone) {
    throw AppError.badRequest('Either email or phone is required');
  }

  if (input.email) {
    const emailTaken = await User.findOne({ where: { email: input.email } });
    if (emailTaken) {
      throw AppError.conflict('Email is already registered');
    }
  }

  if (input.phone) {
    const phoneTaken = await User.findOne({ where: { phone: input.phone } });
    if (phoneTaken) {
      throw AppError.conflict('Phone is already registered');
    }
  }

  const passwordHash = await hashPassword(input.password);

  try {
    const user = await User.create({
      fullName: input.fullName,
      // Empty strings from optional form fields become NULL in the database.
      email: input.email || null,
      phone: input.phone || null,
      passwordHash,
    });
    return { token: signAuthToken({ id: user.id, role: user.role }), user };
  } catch (error) {
    // Safety net in case two concurrent requests race past the checks above.
    if (error instanceof UniqueConstraintError) {
      throw AppError.conflict('Email or phone is already registered');
    }
    throw error;
  }
}

/**
 * Verifies credentials and returns a signed token.
 * Throws AppError.unauthorized (401) on any credential mismatch.
 */
export async function loginUser(input: LoginInput): Promise<AuthResult> {
  const user = await User.findOne({ where: { email: input.email } });

  // Same message whether the email exists or not: never leak which emails are registered.
  if (!user || !(await comparePassword(input.password, user.passwordHash))) {
    throw AppError.unauthorized('Invalid credentials');
  }

  if (user.status === 'suspended') {
    throw AppError.forbidden('Account is suspended');
  }

  user.lastLoginAt = new Date();
  await user.save();

  return { token: signAuthToken({ id: user.id, role: user.role }), user };
}
