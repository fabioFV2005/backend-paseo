# src/services

## 1. Purpose

Business logic, plain and simple. Services know the rules of the marketplace
(what happens when someone registers, logs in, buys, redeems points) but
know NOTHING about HTTP.

## 2. Responsibilities

- `auth.service.ts` — `registerUser()` (duplicates check, password hashing,
  token signing) and `loginUser()` (credential check, status check,
  `lastLoginAt` update).

## 3. Restrictions

- NO Express imports: no `req`, no `res`, no `Request`/`Response` types.
- NO sending responses or setting status codes directly — communicate
  failure by throwing `AppError` factories.
- NO serialization decisions: return models; controllers pass them to views.
- All database queries live here (controllers must never query models).

## 4. How it connects

- Receives: plain typed input (`RegisterInput`, `LoginInput`) from controllers.
- Calls: models (`src/models/`) and utils (`hash`, `jwt`, `AppError`).
- Returns: plain data or model instances; throws `AppError` on failure.

```
controller -> service -> models/utils
                |
                +-- throws AppError.xxx() -> error middleware -> { "error": "..." }
```

## 5. Naming convention

- Files: `<domain>.service.ts`, kebab-case (`auth.service.ts`,
  `store.service.ts`).
- Functions: verb + noun, camelCase (`registerUser`, `approveStore`).
- Input/result interfaces: `<Action>Input`, `<Action>Result`
  (`RegisterInput`, `AuthResult`).

## 6. Minimal example

```ts
export async function approveStore(storeId: number): Promise<Store> {
  const store = await Store.findByPk(storeId);
  if (!store) throw AppError.notFound('Store not found');
  if (store.status === 'approved') throw AppError.conflict('Store is already approved');
  store.status = 'approved';
  await store.save();
  return store;
}
```

## 7. How to add a new one

1. Create `src/services/<domain>.service.ts`.
2. Define the `Input`/`Result` interfaces at the top of the file.
3. Implement functions that receive plain data and return plain data or models.
4. Throw `AppError` with the correct status (400/401/403/404/409) on every
   business rule violation.
5. Wire it from a controller; never the other way around.
6. Update section 2 of this README.
