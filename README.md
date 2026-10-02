# Paseo Aranjuez — Backend

Multi-store marketplace backend with a loyalty program. Built with Node.js,
Express, TypeScript (strict), Sequelize and PostgreSQL, using an MVC
architecture with a services layer.

> **Project status:** under construction, delivered in phases.
> Done: **1 Setup · 2 Server base · 3 User model · 4 Auth (register/login)**.
> Next: 5 Auth middleware + `GET /api/user`.

## Tech stack

| Area        | Choice                                              |
| ----------- | --------------------------------------------------- |
| Runtime     | Node.js >= 20 (ES Modules)                          |
| Language    | TypeScript (strict)                                 |
| HTTP        | Express 4                                           |
| Database    | PostgreSQL 16 (Docker) + Sequelize ORM              |
| Auth        | JWT + bcrypt                                        |
| Validation  | express-validator                                   |
| Uploads     | formidable + sharp (local storage, WebP) (phase 7)  |
| Security    | helmet, cors whitelist, express-rate-limit (phase 9)|

## Requirements

- **Node.js >= 20** and npm.
- **Docker** with the compose plugin (for the PostgreSQL container).

## Installation

```bash
npm install
docker compose up -d        # starts PostgreSQL 16 on localhost:5440
cp .env.example .env        # then edit .env (at least JWT_SECRET)
npm run dev
```

Generate a strong `JWT_SECRET` with: `openssl rand -hex 64`.

## Scripts

| Command              | What it does                                          |
| -------------------- | ----------------------------------------------------- |
| `npm run dev`        | Starts the API with auto-reload (tsx watch)           |
| `npm run build`      | Compiles TypeScript to `dist/`                         |
| `npm start`          | Runs the compiled production build                     |
| `npm run typecheck`  | Type-checks the code without emitting files            |
| `npm run db:check`   | Verifies the PostgreSQL connection using your `.env`   |
| `docker compose up -d`   | Starts the database container                      |
| `docker compose down`    | Stops the database (data persists in the volume)     |

## Environment variables

All variables are validated at startup in `src/config/env.ts`.
The server refuses to boot if a required one is missing.

| Variable         | Required | Default                  | Description                                       |
| ---------------- | -------- | ------------------------ | ------------------------------------------------- |
| `NODE_ENV`       | No       | `development`            | `development`, `test` or `production`             |
| `PORT`           | No       | `4000`                   | HTTP port for the API                             |
| `DB_HOST`        | No       | `localhost`              | PostgreSQL host                                   |
| `DB_PORT`        | No       | `5432`                   | PostgreSQL port (`5440` with the local compose)   |
| `DB_NAME`        | **Yes**  | —                        | Database name                                     |
| `DB_USER`        | **Yes**  | —                        | Database user                                     |
| `DB_PASSWORD`    | **Yes**  | —                        | Database password                                 |
| `JWT_SECRET`     | **Yes**  | —                        | Token signing secret (`openssl rand -hex 64`)     |
| `JWT_EXPIRES_IN` | No       | `1d`                     | Token lifetime (`ms` formats: `1d`, `12h`, `3600`)|
| `CORS_ORIGIN`    | No       | `http://localhost:5173`  | Comma-separated whitelist of frontend origins     |

## Current endpoints

| Method | Path | Auth | Description |
| ------ | ---- | ---- | ----------- |
| GET | `/health` | No | Liveness probe |
| POST | `/api/auth/register` | No | Create account (auto-login) |
| POST | `/api/auth/login` | No | Sign in |

Full request/response reference: [`docs/API.md`](docs/API.md).

## Project structure

```
├── docker-compose.yml  # PostgreSQL 16.2 on port 5440
├── docs/               # ARCHITECTURE.md, API.md, CONVENTIONS.md
├── scripts/            # Development utility scripts (db:check)
└── src/
    ├── index.ts        # Entry point: connects DB and starts listening
    ├── server.ts       # Express app: middlewares + routes + error handlers
    ├── config/         # Validated env vars + Sequelize instance
    ├── types/          # Shared domain types (roles, statuses)
    ├── models/         # Sequelize models + associations
    ├── views/          # Serializers: whitelisted response fields
    ├── services/       # Business logic, no Express dependencies
    ├── controllers/    # Handle req/res, delegate to services
    ├── middlewares/    # validate + global error handler (auth in phase 5)
    ├── validators/     # express-validator rules per resource
    ├── routes/         # One file per resource, mounted under /api
    └── utils/          # AppError, asyncHandler, hash, jwt
```

Every folder inside `src/` has its own `README.md` explaining its purpose,
responsibilities, restrictions and conventions.

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — request flow diagram and design decisions.
- [`docs/API.md`](docs/API.md) — endpoint reference (grows with each phase).
- [`docs/CONVENTIONS.md`](docs/CONVENTIONS.md) — naming, code style, HTTP codes, error format.

## Notes for contributors

- Code, comments and documentation are written in **English**.
- The project uses **ES Modules**: relative imports must include the `.js`
  extension (e.g. `import { env } from './env.js'`).
- Error responses always follow the shape `{ "error": "message" }`.
