# src/middlewares

## 1. Purpose

Functions that sit between the route and the controller: they can reject a
request early (validation, auth) or handle errors globally.

## 2. Responsibilities

- `validate.middleware.ts` — reads `express-validator` results and responds
  400 with the uniform error format if any rule failed.
- `error.middleware.ts` — `notFoundHandler` (404 for unknown routes) and
  `errorHandler` (global catch-all that formats every error as
  `{ "error": "message" }`).
- Future: `auth.middleware.ts` (JWT verification, phase 5), rate limiting.

## 3. Restrictions

- NO database queries or business logic (that belongs to services).
- NO sending success responses — only reject early or call `next()`.
- NO importing controllers or services.
- The global `errorHandler` must keep exactly 4 parameters
  `(err, req, res, next)` or Express will not recognize it.

## 4. How it connects

- Receives: the request after routes (and after validators).
- Calls: `next()` to continue the chain, or `res.status(...)` to reject.
- `errorHandler` is registered last in `src/server.ts` and receives anything
  thrown (or passed to `next(err)`) by the whole chain.

```
routes -> validators -> [middlewares] -> controller
                             |
                             v (on failure)
                     { "error": "..." } response
```

## 5. Naming convention

- One concern per file: `<name>.middleware.ts`, kebab-case.
- Exported functions describe the action: `validate`, `notFoundHandler`,
  `errorHandler`, `requireAuth`.

## 6. Minimal example

```ts
// src/routes/auth.routes.ts
import { validate } from '../middlewares/validate.middleware.js';

authRouter.post('/register', registerValidator, validate, register);
//                                        order matters: ^^^^^^^^^ runs the checks,
//                                        ^^^^^^^^ rejects with 400 if any failed
```

## 7. How to add a new one

1. Create `src/middlewares/<name>.middleware.ts`.
2. Signature: `(req, res, next) => void` — or `(err, req, res, next)` for
   error middlewares.
3. Reject with the uniform format `{ "error": "..." }` or `AppError`.
4. Register it: in specific routes (`router.use`), per-route, or globally
   in `src/server.ts` (error middlewares always last).
5. Update section 2 of this README.
