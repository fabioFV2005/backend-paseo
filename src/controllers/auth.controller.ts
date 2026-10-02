import type { Request, Response } from 'express';
import { loginUser, registerUser, type LoginInput, type RegisterInput } from '../services/auth.service.js';
import { asyncHandler } from '../utils/async-handler.js';
import { toUserResponse } from '../views/user.view.js';

/**
 * Auth controllers. They only translate HTTP <-> service calls:
 * no database queries, no business logic.
 */

/** POST /api/auth/register */
export const register = asyncHandler(async (req: Request, res: Response) => {
  // req.body format was already checked by registerValidator.
  const { token, user } = await registerUser(req.body as RegisterInput);
  res.status(201).json({ token, user: toUserResponse(user) });
});

/** POST /api/auth/login */
export const login = asyncHandler(async (req: Request, res: Response) => {
  const { token, user } = await loginUser(req.body as LoginInput);
  res.status(200).json({ token, user: toUserResponse(user) });
});
