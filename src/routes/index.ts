import { Router } from 'express';
import { authRouter } from './auth.routes.js';

/**
 * API entry router. Every resource router is mounted here under its prefix,
 * and src/server.ts mounts this whole router under /api.
 *
 * Result: POST /api/auth/register, POST /api/auth/login, ...
 */
export const apiRouter = Router();

apiRouter.use('/auth', authRouter);
