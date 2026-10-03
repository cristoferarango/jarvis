// Desarrollo: el núcleo Python y el servidor de Vite a la vez, en una consola.
//
//   npm run dev  ->  http://localhost:5173  (Vite reenvía /ws, /tts, /img… al núcleo en :8787)
//
// Ctrl+C para los dos.
import { spawn } from 'node:child_process'

const procs = []
let stopping = false

function run(name, colour, command, extraEnv = {}) {
  const child = spawn(command, {
    shell: true,
    stdio: ['inherit', 'pipe', 'pipe'],
    env: { ...process.env, ...extraEnv },
  })
  const tag = `\x1b[${colour}m[${name}]\x1b[0m `
  const relay = (stream, out) => {
    let buf = ''
    stream.on('data', (chunk) => {
      buf += chunk.toString()
      const lines = buf.split(/\r?\n/)
      buf = lines.pop() ?? ''
      for (const line of lines) out.write(tag + line + '\n')
    })
  }
  relay(child.stdout, process.stdout)
  relay(child.stderr, process.stderr)
  child.on('exit', (code) => {
    if (!stopping) {
      console.log(`${tag}terminó con código ${code}; parando el resto.`)
      stop(code ?? 1)
    }
  })
  procs.push(child)
}

function stop(code = 0) {
  if (stopping) return
  stopping = true
  for (const p of procs) {
    if (p.exitCode !== null) continue
    if (process.platform === 'win32') spawn('taskkill', ['/pid', String(p.pid), '/T', '/F'], { stdio: 'ignore' })
    else p.kill('SIGTERM')
  }
  setTimeout(() => process.exit(code), 500)
}

process.on('SIGINT', () => stop(0))
process.on('SIGTERM', () => stop(0))

// CRISVIS_DEV=1: en desarrollo la página la sirve Vite y no hay cookie de
// arranque; el núcleo acepta entonces un Origin de los puertos de Vite.
run('núcleo', '36', 'uv run --no-sync python -m crisvis --no-browser', { CRISVIS_DEV: '1' })
run('cara', '35', 'npm run dev -w @crisvis/face')
