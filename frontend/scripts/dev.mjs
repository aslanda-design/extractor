// One command for local development: starts the Python engine API and the web app together.
// Engine output (including every engine event, sanitized) is printed here with an [engine] prefix.
//   npm run dev
import { spawn, spawnSync } from "node:child_process"
import { existsSync } from "node:fs"
import { dirname, join, resolve } from "node:path"
import { createInterface } from "node:readline"
import { fileURLToPath } from "node:url"

const frontend = resolve(dirname(fileURLToPath(import.meta.url)), "..")
const repo = resolve(frontend, "..")
const isWin = process.platform === "win32"
const python = [join(repo, ".venv", isWin ? "Scripts/python.exe" : "bin/python")].find(existsSync) ?? "python"
const port = process.env.ENGINE_PORT ?? "8765"

const COLORS = { engine: "\x1b[35m", web: "\x1b[36m" }
const tag = (name) => `${COLORS[name]}[${name}]\x1b[0m`

function run(name, cmd, args, opts) {
  const child = spawn(cmd, args, { ...opts, stdio: ["ignore", "pipe", "pipe"] })
  for (const stream of [child.stdout, child.stderr]) {
    createInterface({ input: stream }).on("line", (line) => console.log(`${tag(name)} ${line}`))
  }
  child.on("error", (e) => console.log(`${tag(name)} could not start: ${e.message}`))
  child.on("exit", (code) => {
    console.log(`${tag(name)} exited with code ${code}`)
    shutdown(code ?? 1)
  })
  return child
}

const engine = run(
  "engine",
  python,
  ["-u", "ui/run.py", "--no-browser", "--echo-events", "--port", port],
  { cwd: repo, env: { ...process.env, PYTHONUNBUFFERED: "1", PYTHONUTF8: "1" } }
)
const web = run("web", isWin ? "npm.cmd" : "npm", ["run", "dev", "-w", "apps/web"], {
  cwd: frontend,
  shell: isWin,
  env: { ...process.env, ENGINE_URL: `http://127.0.0.1:${port}`, FORCE_COLOR: "0" },
})

let closing = false
function shutdown(code) {
  if (closing) return
  closing = true
  for (const c of [engine, web]) {
    if (!c.pid) continue
    if (isWin) spawnSync("taskkill", ["/pid", String(c.pid), "/T", "/F"], { stdio: "ignore" })
    else c.kill("SIGTERM")
  }
  process.exit(code)
}
process.on("SIGINT", () => shutdown(0))
process.on("SIGTERM", () => shutdown(0))
