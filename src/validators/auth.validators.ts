import { body } from 'express-validator';

/**
 * Validation rules for auth endpoints.
 * They check FORMAT only (is this a valid email? is the password long enough?).
 * Business rules (is this email already registered?) live in the service layer.
 */
export const registerValidator = [
  body('fullName')
    .trim()
    .notEmpty().withMessage('Full name is required')
    .isLength({ max: 120 }).withMessage('Full name must be 120 characters or less'),

  body('email')
    .optional({ values: 'falsy' })
    .isEmail().withMessage('Email must be valid')
    .normalizeEmail(),

  body('phone')
    .optional({ values: 'falsy' })
    .matches(/^\+?[0-9]{8,15}$/).withMessage('Phone must be 8 to 15 digits, optionally starting with +'),

  body('password')
    // 72 is the maximum input length bcrypt can hash; longer passwords would be truncated.
    .isLength({ min: 8, max: 72 }).withMessage('Password must be between 8 and 72 characters'),
];

export const loginValidator = [
  body('email')
    .notEmpty().withMessage('Email is required')
    .isEmail().withMessage('Email must be valid')
    .normalizeEmail(),

  body('password').notEmpty().withMessage('Password is required'),
];
