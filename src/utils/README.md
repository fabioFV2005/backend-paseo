# src/utils

## 1. Purpose

Small, reusable helpers with zero business logic: errors, async wrapper,
password hashing, JWT signing. Any layer may import them.

## 2. Responsibilities

- `app-error.ts` — `AppError` class with named factories per HTTP status
  (`badRequest`, `unauthorized`, `forbidden`, `notFound`, `conflict`).
- `async-handler.ts` — wraps async route handlers so rejections reach the
  global error middleware (Express 4 does not do it natively).
- `hash.ts` — bcrypt hashing and comparison (10 salt rounds).
- `jwt.ts` — signs and verifies auth tokens with the payload `{ id, role }`.

## 3. Restrictions

- NO Express imports, NO `req`/`res` (except the types `asyncHandler` needs).
- NO database access, NO model imports.
- NO business rules (e.g. "is this email taken" belongs to services).
- NO storing state: every util is a pure function or stateless class.

## 4. How it connects

- Receives: primitives (strings, payloads) from any layer.
- Called by: services, controllers and middlewares.
- Calls: nothing inside `src/` except `config/env.ts` (jwt secrets).

```
controllers/services/middlewares  ->  utils  ->  config/env.ts
```

## 5. Naming convention

- One concern per file, kebab-case: `app-error.ts`, `async-handler.ts`.
- Exported functions are verbs in camelCase: `hashPassword()`, `signAuthToken()`.
- Classes in PascalCase: `AppError`.

## 6. Minimal example

```ts
import { AppError } from '../utils/app-error.js';

export async function findStore(id: number) {
  const store = await Store.findByPk(id);
  if (!store) throw AppError.notFound('Store not found'); // -> 404 { "error": "Store not found" }
  return store;
}
```

## 7. How to add a new one

1. Create `src/utils/<name>.ts` exporting pure, typed functions (no `any`).
2. Read configuration from `config/env.ts`, never from `process.env`.
3. Add a brief JSDoc comment explaining what it does and why it exists.
4. Import it where needed; if several layers use it, it belongs here.
5. Update the list in section 2 of this README.
