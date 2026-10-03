// Instalación de Crisvis de principio a fin:
//
//   npm run setup                 todo, incluida la descarga del modelo
//   npm run setup -- --sin-modelo  sin descargar modelos
//   npm run setup -- --modelo=qwen3:4b
//   npm run setup -- --sin-voz     sin el motor de voz clonada (~4 GB)
//   npm run setup -- --sin-vision  sin el modelo que ve la pantalla (~3 GB)
//
// Pasos: uv sync -> npm install -> compilar la cara -> crisvis init ->
// voz clonada (services/voz) -> comprobar Ollama y el modelo -> crisvis doctor.
import { spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { join } from 'node:path'

const args = process.argv.slice(2)
const skipModel = args.includes('--sin-modelo')
const skipVoice = args.includes('--sin-voz')
const skipVision = args.includes('--sin-vision')
const VISION_MODEL = 'qwen3-vl:4b-instruct'
const model = (args.find((a) => a.startsWith('--modelo=')) ?? '--modelo=qwen3:8b').split('=')[1]
const isWin = process.platform === 'win32'

const step = (title) => console.log(`\n\x1b[36m▸ ${title}\x1b[0m`)
const warn = (text) => console.log(`\x1b[33m  ! ${text}\x1b[0m`)
const ok = (text) => console.log(`\x1b[32m  ✓ ${text}\x1b[0m`)

function sh(command, { allowFail = false, capture = false } = {}) {
  const res = spawnSync(command, {
    shell: true,
    stdio: capture ? ['ignore', 'pipe', 'pipe'] : 'inherit',
    encoding: 'utf8',
  })
  if (res.status !== 0 && !allowFail) {
    console.error(`\n\x1b[31m✗ Falló: ${command}\x1b[0m`)
    process.exit(res.status ?? 1)
  }
  return res
}

const has = (cmd) => sh(isWin ? `where ${cmd}` : `command -v ${cmd}`, { allowFail: true, capture: true }).status === 0

function findOllama() {
  if (has('ollama')) return 'ollama'
  const candidates = isWin
    ? [
        join(process.env.LOCALAPPDATA ?? '', 'Programs', 'Ollama', 'ollama.exe'),
        'C:\\Program Files\\Ollama\\ollama.exe',
      ]
    : ['/usr/local/bin/ollama', '/opt/homebrew/bin/ollama', '/usr/bin/ollama']
  const found = candidates.find((p) => p && existsSync(p))
  return found ? `"${found}"` : null
}

step('Python (uv)')
if (!has('uv')) {
  warn('No encuentro `uv`, el gestor de Python que usa el núcleo.')
  console.log(
    isWin
      ? '    Instálalo con:  winget install --id astral-sh.uv -e\n    y abre una consola nueva.'
      : '    Instálalo con:  curl -LsSf https://astral.sh/uv/install.sh | sh',
  )
  process.exit(1)
}
const synced = sh('uv sync', { allowFail: true, capture: true })
if (synced.status !== 0) {
  const why = `${synced.stdout ?? ''}${synced.stderr ?? ''}`
  // Windows con Control inteligente de aplicaciones bloquea el Python temporal
  // en el que uv compila el paquete. Se compila en el entorno del proyecto.
  if (isWin && /4551|Control de aplicaciones|Application Control/i.test(why)) {
    warn('Windows (Control inteligente de aplicaciones) bloqueó la compilación aislada; uso el entorno del proyecto.')
    sh('uv pip install hatchling editables')
    sh('uv sync --no-build-isolation-package crisvis')
  } else {
    console.error(why)
    console.error('\n\x1b[31m✗ Falló: uv sync\x1b[0m')
    process.exit(synced.status ?? 1)
  }
}
ok('Entorno Python listo (OpenJarvis incluido)')

step('Dependencias de la interfaz')
sh('npm install --no-fund --no-audit')
ok('npm install')

step('Compilando la interfaz')
sh('npm run build')
ok('Interfaz compilada en src/crisvis/static')

step('Configuración')
sh('uv run --no-sync python -m crisvis init', { allowFail: true })

step('Voz clonada (XTTS-v2)')
if (skipVoice) {
  warn('Sin voz clonada (--sin-voz). Actívala cuando quieras con: npm run voz:instalar')
} else if (process.platform === 'darwin') {
  warn('La voz clonada solo se instala en Windows y Linux; en macOS se usa la voz del sistema.')
} else {
  console.log('  Descargando PyTorch y el motor de voz… (unos 4 GB, solo la primera vez)')
  const voz = sh('npm run voz:instalar', { allowFail: true })
  if (voz.status === 0) {
    ok('Voz clonada instalada; habla con la voz de serie (services/voz/muestras)')
    console.log('    El modelo XTTS-v2 (~1,8 GB) se descarga solo al primer arranque.')
  } else {
    warn('No se pudo instalar la voz clonada; Crisvis usará la voz del navegador.')
  }
}

step('Motor local (Ollama)')
const ollama = findOllama()
if (!ollama) {
  warn('Ollama no está instalado. Crisvis arranca igual, pero sin cerebro hasta que haya un motor local.')
  console.log(
    isWin
      ? '    Instálalo con:  winget install --id Ollama.Ollama -e\n    y luego:        ollama pull ' + model
      : '    Descárgalo de https://ollama.com/download y luego:  ollama pull ' + model,
  )
} else {
  const list = sh(`${ollama} list`, { allowFail: true, capture: true })
  if (list.status !== 0) {
    warn('Ollama está instalado pero no responde. Ábrelo (o ejecuta `ollama serve`) y repite.')
  } else {
    const names = (list.stdout ?? '')
      .split(/\r?\n/)
      .slice(1)
      .map((l) => l.split(/\s+/)[0])
      .filter(Boolean)
    const chat = names.filter((n) => !/embed/i.test(n))
    if (chat.length) ok(`Modelos disponibles: ${chat.join(', ')}`)
    if (!names.includes(model) && !names.includes(`${model}:latest`)) {
      if (skipModel) {
        warn(`No descargo ${model} (--sin-modelo). Hazlo cuando quieras con: ollama pull ${model}`)
      } else {
        console.log(`  Descargando ${model}… (varios GB, solo la primera vez)`)
        sh(`${ollama} pull ${model}`, { allowFail: true })
      }
    }
    // Para ver la pantalla y la cámara. Crisvis lo elige solo si está instalado.
    if (!names.some((n) => /-vl|vl:|llava|vision/i.test(n))) {
      if (skipModel || skipVision) {
        warn(`Sin modelo de visión. Para ver la pantalla: ollama pull ${VISION_MODEL}`)
      } else {
        console.log(`  Descargando ${VISION_MODEL} para ver la pantalla… (unos 3 GB)`)
        sh(`${ollama} pull ${VISION_MODEL}`, { allowFail: true })
      }
    }
  }
}

step('Diagnóstico')
sh('uv run --no-sync python -m crisvis doctor', { allowFail: true })

console.log('\n\x1b[32mListo.\x1b[0m Arranca con:  \x1b[1mnpm start\x1b[0m   (abre http://127.0.0.1:8787)\n')
