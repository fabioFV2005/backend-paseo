import 'dotenv/config';

/**
 * Reads a required environment variable or fails fast at startup.
 * Failing fast is intentional: a misconfigured server should never boot.
 */
function required(name: string): string {
  const value = process.env[name];
  if (value === undefined || value.trim() === '') {
    throw new Error(`[env] Missing required environment variable: ${name}`);
  }
  return value;
}

/** Reads an optional environment variable, falling back to a default. */
function optional(name: string, fallback: string): string {
  const value = process.env[name];
  return value === undefined || value.trim() === '' ? fallback : value;
}

/** Parses a string as a valid TCP port (1-65535). */
function toPort(name: string, value: string): number {
  const port = Number(value);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error(`[env] ${name} must be an integer between 1 and 65535 (got "${value}")`);
  }
  return port;
}

const nodeEnv = optional('NODE_ENV', 'development');

/**
 * Single source of truth for configuration.
 * Everything outside this file reads `env`, never `process.env`.
 */
export const env = {
  nodeEnv,
  isProduction: nodeEnv === 'production',
  isDevelopment: nodeEnv === 'development',
  port: toPort('PORT', optional('PORT', '4000')),
  db: {
    host: optional('DB_HOST', 'localhost'),
    port: toPort('DB_PORT', optional('DB_PORT', '5432')),
    name: required('DB_NAME'),
    user: required('DB_USER'),
    password: required('DB_PASSWORD'),
  },
  jwt: {
    secret: required('JWT_SECRET'),
    expiresIn: optional('JWT_EXPIRES_IN', '1d'),
  },
  cors: {
    // Comma-separated whitelist parsed into an array, ready for the cors middleware.
    origin: optional('CORS_ORIGIN', 'http://localhost:5173')
      .split(',')
      .map((origin) => origin.trim()),
  },
} as const;

export type Env = typeof env;
