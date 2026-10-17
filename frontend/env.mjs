/**
 * Load the small set of variables the Node front door is allowed to read from env files.
 *
 * The Python service uses pydantic-settings, but `server.mjs` is a plain Node process and
 * Node does not automatically load `.env.local`. The old setup therefore looked configured
 * while the frontend silently kept proxying to its default backend. Keep this loader deliberately
 * narrow: the root `.env` may contain a provider key, and that key must never be copied into the
 * frontend process or a Next client bundle.
 */
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const FRONTEND_ENV_KEYS = new Set([
  "ADAPT_API",
  "HOST",
  "PORT",
  "ADAPT_DEV_ORIGIN",
  "ADAPT_NEXT_DIST",
  "NEXT_TELEMETRY_DISABLED",
  "NODE_ENV",
]);

const FRONTEND_DIR = dirname(fileURLToPath(import.meta.url));
const DEFAULT_FILES = [
  resolve(FRONTEND_DIR, "../.env"),
  resolve(FRONTEND_DIR, ".env"),
  resolve(FRONTEND_DIR, ".env.local"),
];

/** Parse the intentionally small dotenv subset used by the launcher. */
export function parseEnv(text, allowlist = FRONTEND_ENV_KEYS) {
  const values = {};
  for (const rawLine of String(text ?? "").split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const assignment = line.startsWith("export ") ? line.slice(7).trim() : line;
    const equals = assignment.indexOf("=");
    if (equals <= 0) continue;
    const name = assignment.slice(0, equals).trim();
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name) || !allowlist.has(name)) continue;
    let value = assignment.slice(equals + 1).trim();
    if (value.startsWith('"') && value.endsWith('"') && value.length >= 2) {
      try {
        value = JSON.parse(value);
      } catch {
        value = value.slice(1, -1);
      }
    } else if (value.startsWith("'") && value.endsWith("'") && value.length >= 2) {
      value = value.slice(1, -1);
    }
    values[name] = value;
  }
  return values;
}

/**
 * Load root `.env`, then frontend `.env`, then `.env.local`; real process variables win.
 * Returns the resulting environment object so this is easy to test without mutating Node's
 * process environment.
 */
export function loadFrontendEnv({ env = process.env, files = DEFAULT_FILES } = {}) {
  const processKeys = new Set(Object.keys(env));
  const fromFiles = {};
  for (const file of files) {
    if (!existsSync(file)) continue;
    Object.assign(fromFiles, parseEnv(readFileSync(file, "utf8")));
  }
  for (const [name, value] of Object.entries(fromFiles)) {
    if (!processKeys.has(name)) env[name] = value;
  }
  return env;
}

loadFrontendEnv();
