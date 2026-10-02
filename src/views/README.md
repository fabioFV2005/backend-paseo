# src/views

## 1. Purpose

Serializers: the single place that decides WHICH fields of a model the API
exposes and under what name. The last line of defense against data leaks.

## 2. Responsibilities

- `user.view.ts` — `UserResponse` interface + `toUserResponse()` for the
  authenticated user's own data.
- Future: `toPublicUserResponse()` for public profiles (phase 8), store
  views, etc.

## 3. Restrictions

- NEVER expose: `passwordHash`, `deletedAt`, `updatedAt`, `lastLoginAt`,
  internal flags or any credential-like field.
- NO database queries, NO business logic — only shape transformation.
- NO Express imports (views receive models, return plain objects).
- If a field is not explicitly listed in the response interface, it must
  not reach the client.

## 4. How it connects

- Receives: model instances from controllers (which got them from services).
- Called by: controllers, right before `res.json(...)`.

```
service -> controller -> view -> { "token": ..., "user": { ...safe fields } }
```

## 5. Naming convention

- Files: `<name>.view.ts`, kebab-case (`user.view.ts`, `store.view.ts`).
- Interfaces: `<Model>Response` (`UserResponse`, `PublicUserResponse`).
- Functions: `to<Model>Response()` / `to<Model>ListResponse()` for arrays.

## 6. Minimal example

```ts
export function toStoreResponse(store: Store): StoreResponse {
  return {
    id: store.id,
    name: store.name,
    slug: store.slug,
  }; // commissionRate stays internal: it is simply not copied
}
```

## 7. How to add a new one

1. Create `src/views/<name>.view.ts`.
2. Define the `<Model>Response` interface with ONLY the public fields.
3. Write the serializer picking fields one by one (never spread the whole
   model with `...model` — that leaks future columns by accident).
4. Use it in the controller instead of returning the model directly.
5. Update section 2 of this README.
