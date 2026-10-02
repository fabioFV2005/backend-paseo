# API Reference

Base URL: `http://localhost:4000`

All error responses share the same shape: `{ "error": "message" }`.

## Summary

| Method | Path | Auth | Description |
| ------ | ---- | ---- | ----------- |
| GET | `/health` | No | Liveness probe |
| POST | `/api/auth/register` | No | Create account (auto-login) |
| POST | `/api/auth/login` | No | Sign in |

---

## GET /health

Liveness probe for Docker and uptime monitors.

**Response 200**

```json
{ "status": "ok" }
```

---

## POST /api/auth/register

Creates a customer account and returns a token (the user is logged in
immediately). At least one contact channel is required: email or phone.

**Body**

| Field | Type | Required | Rules |
| ----- | ---- | -------- | ----- |
| `fullName` | string | Yes | 1-120 characters |
| `email` | string | Conditional* | Valid email; required if no `phone` |
| `phone` | string | Conditional* | 8-15 digits, optional leading `+`; required if no `email` |
| `password` | string | Yes | 8-72 characters (72 is bcrypt's limit) |

\* The 400 is produced by the service when neither is present.

**Response 201**

```json
{
  "token": "<jwt>",
  "user": {
    "id": 1,
    "fullName": "Fabio Fernandez",
    "email": "fabio@example.com",
    "phone": null,
    "role": "customer",
    "status": "active",
    "avatarUrl": null,
    "personalCode": "DF4B8C4EB8",
    "referralCode": "E840BFA4",
    "createdAt": "2026-10-02T18:48:15.055Z"
  }
}
```

**Errors**

| Status | When | Example |
| ------ | ---- | ------- |
| 400 | Format rules failed | `{ "error": "Email must be valid. Password must be between 8 and 72 characters" }` |
| 400 | Neither email nor phone | `{ "error": "Either email or phone is required" }` |
| 409 | Email already registered | `{ "error": "Email is already registered" }` |
| 409 | Phone already registered | `{ "error": "Phone is already registered" }` |

---

## POST /api/auth/login

Verifies credentials, updates `last_login_at` and returns a fresh token.

**Body**

| Field | Type | Required | Rules |
| ----- | ---- | -------- | ----- |
| `email` | string | Yes | Valid email |
| `password` | string | Yes | Non-empty |

**Response 200**

```json
{
  "token": "<jwt>",
  "user": { "id": 1, "fullName": "Fabio Fernandez", "...": "..." }
}
```

**Errors**

| Status | When | Example |
| ------ | ---- | ------- |
| 400 | Format rules failed | `{ "error": "Email is required" }` |
| 401 | Wrong email or password | `{ "error": "Invalid credentials" }` |
| 403 | Account suspended | `{ "error": "Account is suspended" }` |

> Security note: the same `401 Invalid credentials` message is returned for
> unknown emails and wrong passwords, so the API never reveals which emails
> are registered.

---

## Authentication (from phase 5)

Protected endpoints expect the token in the `Authorization` header:

```
Authorization: Bearer <jwt>
```
