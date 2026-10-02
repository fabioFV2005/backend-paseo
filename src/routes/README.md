# src/routes

## 1. Purpose

URL definitions. Routes declare WHICH middlewares, validators and
controllers run for each endpoint — and nothing else.

## 2. Responsibilities

- `auth.routes.ts` — `POST /register` and `POST /login` with their
  validator chains.
- `index.ts` — the `apiRouter`: mounts every resource router under its
  prefix (`/auth`, `/stores`, ...). `src/server.ts` mounts `apiRouter`
  under `/api`.

## 3. Restrictions

- NO logic: no business rules, no queries, no responses.
- NO importing services or models — only controllers, middlewares,
  validators and other routers.
- Middleware order is part of the contract: validators -> validate ->
  (auth) -> controller.

## 4. How it connects

- Receives: requests from `src/server.ts` (`app.use('/api', apiRouter)`).
- Calls: validators, middlewares and finally one controller per route.

```
server.ts -> /api -> routes/index.ts -> /auth -> routes/auth.routes.ts
                                              -> POST /register -> controller
```

## 5. Naming convention

- Files: `<resource>.routes.ts`, kebab-case (`auth.routes.ts`).
- Exported routers: `<resource>Router` (`authRouter`).
- Paths: plural nouns for resources (`/stores`), verbs only for actions
  without a resource (`/login`, `/register`).

## 6. Minimal example

```ts
import { Router } from 'express';

export const storeRouter = Router();

storeRouter.get('/', listStores);
storeRouter.post('/', createStoreValidator, validate, requireAuth, createStore);
//                 ^ format checks  ^ rejects 400  ^ auth  ^ handler
```

## 7. How to add a new one

1. Create `src/routes/<resource>.routes.ts` exporting `<resource>Router`.
2. Declare each route in this order: path, validators, `validate`, auth
   middleware (if protected), controller.
3. Mount the router in `src/routes/index.ts`:
   `apiRouter.use('/<resource>', <resource>Router);`
4. Document the new endpoints in `docs/API.md`.
5. Update section 2 of this README.
