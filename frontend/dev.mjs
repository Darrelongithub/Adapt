/** One command, both servers. Reuse adapt host's LAN trust and child cleanup. */
import { spawn, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

export const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

export function pythonCommand(env = process.env, exists = existsSync) {
  if (env.ADAPT_PYTHON) return env.ADAPT_PYTHON;
  const executable = process.platform === "win32" ? ["Scripts", "python.exe"] : ["bin", "python"];
  for (const directory of [join(root, ".venv"), env.VIRTUAL_ENV].filter(Boolean)) {
    const candidate = join(directory, ...executable);
    if (exists(candidate)) return candidate;
  }
  return process.platform === "win32" ? "python" : "python3";
}

export function hostArgs(env = process.env, args = [], { dev = true } = {}) {
  return ["-m", "adapt.cli", "host", ...(dev ? ["--dev"] : []), "--port", env.PORT || "3000", ...args];
}

export function main({ dev = true } = {}) {
  const python = pythonCommand();
  const cwd = join(root, "backend");
  const check = spawnSync(python, ["-c", "import adapt.cli, uvicorn, alembic"], {
    cwd, stdio: "pipe", encoding: "utf8",
  });
  if (check.error || check.status !== 0) {
    console.error(`Cannot load the ADAPT backend with ${python}.`);
    console.error("One-time setup from the repository root:\n" +
      "  python3 -m venv .venv\n" +
      "  .venv/bin/python -m pip install -e './backend[dev]'\n" +
      "Then run npm run dev again. ADAPT_PYTHON can select a different Python executable.");
    // No environment/config values or captured Python output are printed (they can contain keys).
    process.exitCode = 1;
    return;
  }
  const child = spawn(python, hostArgs(process.env, process.argv.slice(2), { dev }), {
    cwd, stdio: "inherit", detached: process.platform !== "win32",
  });
  const stop = signal => {
    if (child.exitCode === null && child.signalCode === null) child.kill(signal);
  };
  const onInterrupt = () => stop("SIGINT");
  const onTerminate = () => stop("SIGTERM");
  process.on("SIGINT", onInterrupt);
  process.on("SIGTERM", onTerminate);
  child.once("error", error => {
    console.error(`Could not start the host process: ${error.message}`);
    process.exitCode = 1;
  });
  child.once("exit", (code, signal) => {
    process.off("SIGINT", onInterrupt);
    process.off("SIGTERM", onTerminate);
    process.exitCode = code ?? (signal === "SIGINT" ? 130 : 1);
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) main();
