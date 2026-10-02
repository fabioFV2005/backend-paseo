# src/controllers

## 1. Purpose

Thin HTTP translators: read the request, call the right service, serialize
the result through a view, send the response. Nothing more.

## 2. Responsibilities

- `auth.controller.ts` — `register` (201 + token + user) and `login`
  (200 + token + user).

## 3. Restrictions

- NO database queries — that is the service layer's job.
- NO business rules ("can this user do X?" belongs to services).
- NO returning models directly: always pass them through `src/views/`.
- Every handler must be wrapped in `asyncHandler` so errors reach the
  global error middleware.

## 4. How it connects

- Receives: the request after routes, validators and middlewares.
- Calls: one service function per handler, then one view per response.

```
route -> validator -> validate -> controller -> service
                                      |
                                      +-> view -> res.status(...).json(...)
```

## 5. Naming convention

- Files: `<resource>.controller.ts`, kebab-case.
- Handlers: camelCase verbs (`register`, `login`, `getProfile`,
  `updateProfile`).

## 6. Minimal example

```ts
export const getStores = asyncHandler(async (req: Request, res: Response) => {
  const stores = await listStores();
  res.status(200).json({ stores: stores.map(toStoreResponse) });
});
```

## 7. How to add a new one

1. Create the service first (`src/services/<domain>.service.ts`).
2. Create `src/controllers/<resource>.controller.ts` with one exported
   handler per endpoint, wrapped in `asyncHandler`.
3. Cast `req.body` to the service `Input` type (validators already checked
   its format).
4. Pick the correct status: 200 read/update, 201 create, 204 delete.
5. Register the handlers in `src/routes/<resource>.routes.ts`.
6. Update section 2 of this README and `docs/API.md`.
