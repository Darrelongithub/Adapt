import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { loadFrontendEnv, parseEnv } from "./env.mjs";

test("the frontend parser accepts copyable dotenv values but ignores secrets", () => {
  const parsed = parseEnv(`
    ADAPT_API=http://127.0.0.1:8123
    HOST=0.0.0.0
    PORT=3010
    ADAPT_DEV_ORIGIN="localhost,127.0.0.1"
    ADAPT_GEMINI_API_KEY=must-not-enter-node
    NOT_A_FRONTEND_SETTING=yes
  `);

  assert.deepEqual(parsed, {
    ADAPT_API: "http://127.0.0.1:8123",
    HOST: "0.0.0.0",
    PORT: "3010",
    ADAPT_DEV_ORIGIN: "localhost,127.0.0.1",
  });
});

test("local frontend values override file defaults while process variables win", () => {
  const directory = mkdtempSync(join(tmpdir(), "adapt-env-test-"));
  const root = join(directory, "root.env");
  const frontend = join(directory, "frontend.env");
  const local = join(directory, "local.env");
  writeFileSync(root, "ADAPT_API=http://root:8000\nPORT=3000\n");
  writeFileSync(frontend, "ADAPT_API=http://frontend:8000\nPORT=3001\n");
  writeFileSync(local, "ADAPT_API=http://local:8000\n");

  const result = loadFrontendEnv({
    env: { HOST: "process-host" },
    files: [root, frontend, local],
  });
  assert.deepEqual(result, {
    HOST: "process-host",
    ADAPT_API: "http://local:8000",
    PORT: "3001",
  });

  const processWins = loadFrontendEnv({
    env: { ADAPT_API: "http://process:8000" },
    files: [root, frontend, local],
  });
  assert.equal(processWins.ADAPT_API, "http://process:8000");
});
