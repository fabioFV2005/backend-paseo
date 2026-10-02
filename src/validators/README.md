# src/validators

## 1. Purpose

`express-validator` rule chains that check the FORMAT of incoming data
before it touches any business logic.

## 2. Responsibilities

- `auth.validators.ts` — `registerValidator` (fullName, email, phone,
  password format) and `loginValidator` (email + password present).

## 3. Restrictions

- ONLY format checks: lengths, patterns, types, allowed values.
- NO database checks ("email already taken" is a business rule: it belongs
  to the service layer, which answers 409).
- NO sending responses: the `validate` middleware does that.
- Every rule must end with a clear `.withMessage(...)` in English — that
  message is what the client receives.

## 4. How it connects

- Receives: the raw request body, right after the route matches.
- Called by: route definitions, always followed by the `validate`
  middleware.

```
route -> validators (check format) -> validate (400 or next()) -> controller
```

## 5. Naming convention

- Files: `<resource>.validators.ts`, kebab-case.
- Exported chains: `<action>Validator` (`registerValidator`,
  `updateProfileValidator`).

## 6. Minimal example

```ts
import { body } from 'express-validator';

export const createStoreValidator = [
  body('name')
    .trim()
    .notEmpty().withMessage('Store name is required')
    .isLength({ max: 80 }).withMessage('Store name must be 80 characters or less'),
];
```

## 7. How to add a new one

1. Create (or extend) `src/validators/<resource>.validators.ts`.
2. Export a chain array named `<action>Validator`.
3. Mount it in the route BEFORE the `validate` middleware and the controller.
4. Document the rules in `docs/API.md` (body section of the endpoint).
5. Update section 2 of this README.
