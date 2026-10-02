# src/types

## 1. Purpose

Shared TypeScript domain types and type augmentations, kept free of runtime
dependencies so any layer can import them safely.

## 2. Responsibilities

- `user.types.ts` — `USER_ROLES` / `UserRole` and `USER_STATUSES` /
  `UserStatus`: the single source of truth for role and status values.
- Future: `express.d.ts` augmenting `Request` with `req.user` (phase 5).

## 3. Restrictions

- NO imports from models, services, controllers or config (only `type` level
  imports if strictly needed, and never creating cycles).
- NO business logic — this layer only describes shapes and constant lists.
- NO duplicated definitions: if a type describes the User domain, it lives
  here exactly once and everyone imports it.

## 4. How it connects

- Receives: nothing (it is the bottom of the dependency graph).
- Called by: models (enum values), services (signatures), views (response
  interfaces), middlewares (request augmentation).

```
models / services / views / middlewares  ->  types
```

## 5. Naming convention

- Files: `<domain>.types.ts`, kebab-case (`user.types.ts`, `store.types.ts`).
- Constant lists: `UPPER_SNAKE_CASE` with `as const` (`USER_ROLES`).
- Derived types: PascalCase (`UserRole = (typeof USER_ROLES)[number]`).

## 6. Minimal example

```ts
// src/types/store.types.ts
export const STORE_STATUSES = ['pending', 'approved', 'rejected'] as const;
export type StoreStatus = (typeof STORE_STATUSES)[number];
```

```ts
// usage anywhere else
import type { StoreStatus } from '../types/store.types.js';
```

## 7. How to add a new one

1. Create `src/types/<domain>.types.ts`.
2. Prefer `as const` arrays + derived union types over TS `enum` (they work
   better with Sequelize `DataTypes.ENUM(...LIST)` and with JSON).
3. Keep runtime constants next to their derived type in the same file.
4. Update section 2 of this README.
