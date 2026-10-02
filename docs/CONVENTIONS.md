# Conventions

## Language

Everything is written in **English**: code, comments, commit messages,
documentation and API error messages.

## File and folder naming

| Item | Convention | Example |
| ---- | ---------- | ------- |
| Files | kebab-case + layer suffix | `auth.controller.ts`, `user.model.ts` |
| Classes / models | PascalCase singular | `User`, `AppError` |
| Functions / variables | camelCase verbs | `registerUser()`, `toUserResponse()` |
| Constants | UPPER_SNAKE_CASE | `USER_ROLES`, `SALT_ROUNDS` |
| Router exports | `<resource>Router` | `authRouter` |
| DB tables / columns | snake_case plural tables | `users`, `personal_code` |

## Imports

- ES Modules only. Relative imports always include the `.js` extension:
  `import { env } from '../config/env.js'`.
- Import order: node builtins -> external packages -> internal layers.
- Import domain types from `src/types/`, never re-declare them elsewhere.

## TypeScript

- `strict` mode, **no `any`** (cast to a domain type instead).
- No TS `enum`: use `as const` arrays + derived union types.
- Exported functions declare their return type.

## Layer rules (the pipeline)

```
Request -> routes -> validators -> validate -> (auth) -> controller -> service -> model
                                                                          |
                                                            view <- controller
                                                            |
                                                          Response
```

- Queries only in services. `req`/`res` only in controllers/middlewares.
- Serialization only in views. Errors only via `AppError` + error middleware.

## HTTP status codes

| Code | Use it for |
| ---- | ---------- |
| 200 | Successful read / update / login |
| 201 | Successful creation (register, new resource) |
| 204 | Successful delete with no body |
| 400 | Input format errors (validators) |
| 401 | Missing or invalid credentials/token |
| 403 | Authenticated but not allowed (e.g. suspended account) |
| 404 | Resource or route not found |
| 409 | Uniqueness / state conflicts (duplicate email, already approved) |
| 429 | Rate limit exceeded (phase 9) |
| 500 | Unexpected server error (never leak details) |

## Error format

Always `{ "error": "message" }`. Thrown as `AppError.<factory>(message)`
from services; formatted by the global error middleware. Multiple
validation messages are joined with `. ` in a single 400 response.

## Comments

- Brief, in English, and only where they add non-obvious information
  (the "why", never the "what").
- Public functions carry a one-line JSDoc.

## Responses

- JSON objects, never top-level arrays: `{ "stores": [...] }`.
- Resource payloads come from views; timestamps in ISO 8601 (JSON default).
