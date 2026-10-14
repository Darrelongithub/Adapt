import assert from "node:assert/strict";
import { test } from "node:test";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { hostArgs, pythonCommand, root } from "./dev.mjs";

const executable = process.platform === "win32" ? ["Scripts", "python.exe"] : ["bin", "python"];

test("uses the repository venv without requiring shell activation", () => {
  const expected = join(root, ".venv", ...executable);
  assert.equal(pythonCommand({}, p => p === expected), expected);
});

test("supports an explicit Python override and an activated venv", () => {
  assert.equal(pythonCommand({ ADAPT_PYTHON: "/custom/python" }), "/custom/python");
  const expected = join("/active", ...executable);
  assert.equal(pythonCommand({ VIRTUAL_ENV: "/active" }, p => p === expected), expected);
});

test("launches both services in dev mode, forwarding the port and LAN override", () => {
  assert.deepEqual(hostArgs({}), ["-m", "adapt.cli", "host", "--dev", "--port", "3000"]);
  assert.deepEqual(hostArgs({ PORT: "3010" }, ["--ip", "192.168.1.20"]),
    ["-m", "adapt.cli", "host", "--dev", "--port", "3010", "--ip", "192.168.1.20"]);
});

test("missing Python fails with setup instructions, rather than starting half the app", () => {
  const result = spawnSync(process.execPath, [join(root, "frontend", "dev.mjs")], {
    env: { ...process.env, ADAPT_PYTHON: join(root, "nonexistent-python") }, encoding: "utf8",
  });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /One-time setup/);
  assert.match(result.stderr, /pip install/);
});

test("production hosting keeps the same port/arguments without enabling dev compilation", () => {
  assert.deepEqual(hostArgs({ PORT: "3001" }, ["--ip", "192.168.1.20"], { dev: false }),
    ["-m", "adapt.cli", "host", "--port", "3001", "--ip", "192.168.1.20"]);
});
