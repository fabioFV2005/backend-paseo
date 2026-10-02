# src/config

## 1. Purpose

Application configuration lives here: validated environment variables and
external service connections (PostgreSQL today, file storage tomorrow).
This layer is the **only** place allowed to read `process.env`.

## 2. Responsibilities

- Load and validate environment variables at startup (`env.ts`).
- Fail fast with a clear message when a required variable is missing or invalid.
- Create and export the single shared Sequelize instance (`db.ts`).
- Expose a `connectDatabase()` function the server calls before listening.
- Hold future service clients (e.g. local file storage configuration).

## 3. Restrictions

- NO Express imports (`req`, `res`, routers, middlewares).
- NO business logic, models, or SQL queries (models own those).
- NO reading `process.env` outside `env.ts` — the rest of the app imports `env`.
- NO default secrets: sensitive values (`DB_PASSWORD`, `JWT_SECRET`) are always required.

## 4. How it connects

- Receives: nothing from other layers. It only reads the `.env` file and `process.env`.
- Called by: `src/index.ts` calls `connectDatabase()`; any layer may import
  `env` or `sequelize` (mainly `src/models/`).

```
.env  ->  config/env.ts  ->  config/db.ts  ->  src/index.ts / src/models/
```

## 5. Naming convention

- One file per service or concern, kebab-case: `env.ts`, `db.ts`, `storage.ts`.
- Exported instances use camelCase: `sequelize`.
- Setup functions use verbs: `connectDatabase()`.

## 6. Minimal example

```ts
// src/config/example.ts
import { env } from './env.js';

export const exampleClient = {
  url: env.db.host, // always read config from `env`, never from process.env
};
```

## 7. How to add a new one

1. Add the new variables to `.env.example` (and to your local `.env`).
2. Validate them in `env.ts` using `required()` / `optional()` and expose them
   under a descriptive key of the `env` object.
3. Create `src/config/<service>.ts` that builds the client using `env`.
4. Export a `connect<Service>()` function if the service needs a startup check,
   and call it from `src/index.ts`.
5. Update this README and the root `README.md` environment variables table.
