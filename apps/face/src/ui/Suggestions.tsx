import { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { useStore } from '../store'
import { persona } from '../lib/persona'

/**
 * Rotating example commands, shown only while idle.
 *
 * A voice interface has no menus — nothing tells you what it can do. This is
 * the affordance. It disappears the moment the assistant is doing anything, so
 * it never competes with the answer.
 *
 * Each line is phrased the way you'd actually say it, and only promises what
 * the OpenJarvis brain ships with out of the box.
 */
const EXAMPLES = [
  'qué ha pasado hoy en el mundo de la IA',
  'recuerda que mi reunión con Ana es el jueves',
  'qué te dije sobre la reunión con Ana',
  'cuánto es el 18 % de 2.450',
  'busca la mejor cafetería cerca de Sol',
  'abre la portada de Hacker News',
  'pon el reactor en rojo',
  'echa un vistazo y dime qué ves',
  'qué tiempo hará mañana en Madrid',
  'lee el archivo notas.txt de mi escritorio',
]

const ROTATE_MS = 4200

export function Suggestions() {
  const phase = useStore((s) => s.phase)
  const turns = useStore((s) => s.turns)
  const [i, setI] = useState(0)

  useEffect(() => {
    const id = setInterval(() => setI((n) => (n + 1) % EXAMPLES.length), ROTATE_MS)
    return () => clearInterval(id)
  }, [])

  // Only while genuinely idle, and only until the first exchange — once the
  // user knows how it works, the prompt is just clutter.
  if (phase !== 'dormant' || turns.length > 0) return null

  return (
    <div className="suggest">
      <span className="suggest-lead">pruebe</span>
      <AnimatePresence mode="wait">
        <motion.span
          key={i}
          className="suggest-text"
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -6 }}
          transition={{ duration: 0.35 }}
        >
          «{persona().name.toLowerCase()}, {EXAMPLES[i]}»
        </motion.span>
      </AnimatePresence>
    </div>
  )
}
