// Comprueba que las listas de tramas de src/index.ts coinciden con frames.json.
import { readFileSync } from 'node:fs'

const frames = JSON.parse(readFileSync(new URL('./frames.json', import.meta.url), 'utf8'))
const source = readFileSync(new URL('./src/index.ts', import.meta.url), 'utf8')

function listFrom(name) {
  const m = new RegExp(`export const ${name} = \\[([\\s\\S]*?)\\]`).exec(source)
  if (!m) throw new Error(`no se encontró ${name} en src/index.ts`)
  return [...m[1].matchAll(/'([a-z]+)'/g)].map((x) => x[1])
}

let failed = false
for (const [side, constant] of [
  ['client', 'CLIENT_FRAMES'],
  ['core', 'CORE_FRAMES'],
]) {
  const ts = listFrom(constant)
  const json = frames[side]
  if (JSON.stringify(ts) !== JSON.stringify(json)) {
    console.error(`[protocolo] ${constant} = ${ts.join(',')} pero frames.json.${side} = ${json.join(',')}`)
    failed = true
  }
}
if (failed) process.exit(1)
console.log('[protocolo] TypeScript y frames.json coinciden.')
