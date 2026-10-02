# src/models

## 1. Purpose

Sequelize models: the typed representation of each PostgreSQL table, plus
their associations. This is the only layer that knows how data is stored.

## 2. Responsibilities

- `user.model.ts` — `users` table: attributes, column types, unique
  constraints, model-level validations (e.g. email OR phone required) and
  code generators (`personalCode`, `referralCode`).
- `index.ts` — model registry: defines associations and re-exports every
  model, plus `syncModels()` (development-only schema sync).

## 3. Restrictions

- NO Express imports, NO `req`/`res`.
- NO HTTP concerns (status codes, error messages for clients) — models throw
  plain/Sequelize errors; services translate them to `AppError`.
- NO querying models from controllers: only services may import models.
- NO migrations logic here (phase 10); `syncModels()` is development-only.

## 4. How it connects

- Receives: the `sequelize` instance from `src/config/db.ts` and shared
  types from `src/types/`.
- Called by: services (only layer allowed to run queries).

```
config/db.ts + types/  ->  models/  ->  services/
```

## 5. Naming convention

- Files: `<name>.model.ts`, kebab-case (`user.model.ts`, `store.model.ts`).
- Classes: PascalCase singular (`User`, `Store`), table names plural
  snake_case (`users`, `stores`).
- Attributes: camelCase in code; the global `underscored` setting converts
  them to snake_case columns automatically.

## 6. Minimal example

```ts
export class Store extends Model<InferAttributes<Store>, InferCreationAttributes<Store>> {
  declare id: CreationOptional<number>;
  declare name: string;
}

Store.init(
  { id: { type: DataTypes.BIGINT, autoIncrement: true, primaryKey: true },
    name: { type: DataTypes.TEXT, allowNull: false } },
  { sequelize, tableName: 'stores' },
);
```

## 7. How to add a new one

1. Define its shared enums/types in `src/types/<domain>.types.ts` if needed.
2. Create `src/models/<name>.model.ts` following the `User` pattern:
   `CreationOptional` for auto-generated fields, explicit `createdAt` /
   `updatedAt` / `deletedAt` declarations.
3. Register the model and its associations in `src/models/index.ts`.
4. Restart the dev server: `syncModels()` creates the table in development.
5. Create the matching view (`src/views/<name>.view.ts`) so sensitive
   columns are never leaked.
6. Update section 2 of this README and `docs/ARCHITECTURE.md` if the data
   model changed significantly.
