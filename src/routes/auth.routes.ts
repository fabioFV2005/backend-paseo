import { Router } from 'express';
import { login, register } from '../controllers/auth.controller.js';
import { validate } from '../middlewares/validate.middleware.js';
import { loginValidator, registerValidator } from '../validators/auth.validators.js';

/**
 * Auth routes. Pattern per route:
 * validators -> validate middleware -> controller
 */
export const authRouter = Router();

authRouter.post('/register', registerValidator, validate, register);
authRouter.post('/login', loginValidator, validate, login);
