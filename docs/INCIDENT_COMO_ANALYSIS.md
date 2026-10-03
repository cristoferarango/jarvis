# Análisis del incidente «como»

Fecha del evento: 2026-10-03, hacia las 15:25 UTC (10:25 hora local, UTC-5).
Instancia afectada: la instancia de **prueba** de CRISVIS en `127.0.0.1:8790`
(código de las Fases 0 a 3, antes del hardening), con `CRISVIS_HOME` en
`%TEMP%\crisvis-smoke`. Esa instancia ya está cerrada.

## 1. Resumen

En la consola de la instancia de prueba apareció un turno de usuario con el
texto «como», seguido del estado PROCESANDO. Nadie lo pidió de forma
consciente en la conversación con el agente.

**Conclusión:** el origen exacto de «como» **no se puede determinar** con la
evidencia disponible. Lo que sí queda establecido:

- No lo generó ninguna herramienta del agente (ni scripts, ni CDP, ni pruebas).
- Llegó por la sesión de WebSocket `wbnTJSNN`, la de la pestaña del navegador
  integrado de Cursor, no por HTTP ni por otro proceso.
- Ocurrió dentro de la ventana de escucha de seguimiento que la interfaz abre
  tras cada respuesta (11 s), con el reconocimiento de voz del navegador activo.
- No ejecutó ninguna herramienta ni acción: no hay rastro en ninguna auditoría.

**No se atribuye a ninguna persona.**

## 2. Evidencia disponible

| Fuente | Qué contiene | Qué no contiene |
|---|---|---|
| `%TEMP%\crisvis-smoke\auditoria-adaptador.jsonl` | Cada informe (`briefing.request`, `briefing.source`, `briefing.done`) con su sesión | Las tramas `ask`: el código anterior no registraba las entradas de texto |
| `%TEMP%\crisvis-smoke\cerebro\audit.db` y `memoria_crisvis.db` | Llamadas a herramientas y memoria del cerebro | Nada del evento: su última escritura es de las 14:45:33 UTC |
| Captura `page-2026-10-03T15-25-47-488Z.png` | Estado de la interfaz a las 15:25:47 UTC | — |
| Historial del agente | Cada acción del agente sobre esa instancia y su hora aproximada | — |

## 3. Línea de tiempo (UTC)

| Hora | Sesión | Hecho | Origen |
|---|---|---|---|
| 14:39:29 y 14:39:44 | (ninguna) | Dos informes completos | Scripts de prueba del agente, por HTTP y sin sesión |
| 14:45:46–14:45:53 | `YqwgDPKg` | Informe, aprobación `correo.send` (resultado `simulated`) y bloqueo de `correo.delete` | Script de extremo a extremo del agente, por WebSocket |
| 14:46:37–14:46:43 | `wbnTJSNN` | Informe «Informe del día» | El agente pulsó INFORME en la pestaña del navegador integrado |
| 14:46:48–14:47:41 | `wbnTJSNN` | Siete capturas de las pestañas del informe | El agente; última interacción hasta las 15:25:44 |
| **15:25:03.311** | `wbnTJSNN` | `briefing.request` con trigger «Informe del día» | **No determinado.** Ninguna herramienta del agente actuó a esa hora |
| 15:25:09.293 | `wbnTJSNN` | `briefing.done` (n8n en timeout tras 5984 ms; docker no disponible) | — |
| ~15:25:09–15:25:30 | `wbnTJSNN` | La interfaz lee el resumen con la voz del navegador «Microsoft Pablo» y abre la escucha de seguimiento | Comportamiento normal de `respond()` |
| entre ~15:25:10 y 15:25:44 | `wbnTJSNN` | Aparece el turno «USTED: como» y el estado PROCESANDO | **No determinado** (ver §4) |
| ~15:25:44 | `wbnTJSNN` | El agente ejecuta por CDP un clic en el primer botón «Actualizar» del panel del informe | Agente. Ver §5 |
| 15:25:47.488 | — | Captura: dos resúmenes, el turno «Informe del día», el turno «como» y PROCESANDO; el panel del informe está cerrado | Agente (captura) |
| 15:26 (10:26 local) | — | El usuario escribe en el chat que la voz que oye no es la correcta | Mensaje del usuario |

Después de las 15:25:09 no hay ningún otro `briefing.request` en la auditoría.

## 4. Qué pudo producir «como»

La captura muestra «como» como un **turno de usuario ya enviado** (etiqueta
USTED) y el estado PROCESANDO, es decir, la interfaz llamó a `respond("como")`
y mandó una trama `ask` al núcleo. En el código de entonces solo hay tres
caminos que lo hacen:

1. **Voz (`onUtterance`)**. Tras leer el resumen, `respond()` deja el micrófono
   abierto `FOLLOW_UP_MS = 11000` ms. El motor de voz de esa instancia era el
   `SpeechRecognition` del navegador: no había faster-whisper instalado en el
   entorno y la voz activa era «Microsoft Pablo», no ElevenLabs. Cualquier
   palabra reconocida en esa ventana se enviaba como orden.
2. **Texto escrito en la consola (`onTyped`)**.
3. **Un script en la página**. No hay evidencia de que existiera; las únicas
   ejecuciones de JavaScript del agente en esa pestaña están en el historial y
   ninguna escribe texto.

Debilidad concreta encontrada al revisar el camino 1: el filtro de eco
(`isEcho` en `voice.ts`) **nunca** descarta un enunciado de una sola palabra
(`if (all.length < 2) return false`), y en modo seguimiento una sola palabra
basta para iniciar un turno. «como» es justo una palabra funcional sin
intención propia, del tipo que deja pasar un sonido de fondo, una conversación
cercana o un artefacto del reconocedor. El texto que se leía en voz alta no
contiene «como», así que no hay evidencia de que fuera eco de la propia voz.

Ninguno de los tres caminos se puede confirmar ni descartar del todo, porque la
instancia no registraba las entradas.

## 5. Acciones del agente cerca del evento

- A las ~15:25:44 el agente ejecutó en esa pestaña, por CDP:
  `document.querySelectorAll('.briefing button')` → primer botón con el texto
  «Actualizar» → `click()`, y tomó una captura 3 s después. Su intención era
  refrescar la pestaña Integraciones (solo lectura: `status` y `audit`).
- Ese clic **no pudo producir «como»**: el botón «Actualizar» solo pide estado,
  refresca fuentes o lanza `onRun()`, que envía el literal «Informe del día».
  Además, la auditoría no registra ningún informe después de las 15:25:09, y en
  la captura el panel ya estaba cerrado, así que es probable que el clic no
  encontrara ningún botón.
- El informe de las 15:25:03 tampoco lo lanzó el agente: su última acción en
  la pestaña antes de ese momento fue la captura de las 14:47:41. El trigger es
  exactamente el literal del botón (`onRun={() => onTyped('Informe del día')}`),
  compatible con un clic en INFORME, «Nuevo informe» o «Actualizar» de la
  pestaña Resumen, o con el mismo texto escrito a mano.
- Al ver «como», el agente dejó de manejar el navegador para no interferir.

## 6. Impacto

- No se ejecutó ninguna herramienta, acción ni conector: ni la auditoría del
  adaptador ni la del cerebro registran nada después del informe.
- El turno llegó al modelo (qwen3:8b) como pregunta. La respuesta no quedó
  registrada.
- La instancia corría con datos de prueba y `DRY_RUN=true`, sin cuentas reales.

## 7. Causas que permitieron el evento y medidas aplicadas

| Causa | Medida | Dónde | Prueba |
|---|---|---|---|
| Las entradas (`ask`) no dejaban rastro: no se puede saber de dónde vino un texto | Telemetría de cada entrada: hora, `event_id`, sesión, fuente declarada (`voz`, `voz_activacion`, `teclado`, `boton`), canal, Origin, HMAC-SHA256 del texto con clave local (no el texto), longitud, autenticación, aceptada o rechazada y motivo | `security/telemetry.py`, `<home>/telemetria-entradas.jsonl` | `test_hardening_telemetry.py`, `test_hardening_ws.py::test_valid_client_works` |
| La interfaz pintaba el turno del usuario antes de que el núcleo lo aceptara | El núcleo responde con una trama `input` (aceptada o rechazada) y la interfaz solo muestra el turno tras la aceptación | `gateway/ws.py`, `lib/brain.ts`, `App.tsx` | `test_hardening_ws.py` |
| El WebSocket aceptaba a cualquier cliente que imitara un Origin local | Ticket de un solo uso, Host y Origin obligatorios, esquema estricto de tramas | `security/auth.py`, `gateway/ws.py`, `gateway/frames.py` | `test_hardening_ws.py` (21 pruebas) |
| Una palabra funcional suelta se enviaba como orden | `isNoiseUtterance()`: un enunciado de voz de una sola palabra funcional («como», «que», «y», «eh»…) se descarta como ruido. «sí», «no» y «para» siguen funcionando: las confirmaciones y el Stop se tratan antes | `lib/guardrails.ts`, `App.tsx` (`onUtterance`) | `apps/face/test/guardrails.test.ts` |
| La instancia de prueba quedó encendida sin uso | Cerrada. Procedimiento en `LOCAL_OPERATIONS.md`: las instancias de prueba se cierran al terminar | — | — |

## 8. Limitaciones y riesgo residual

- La fuente (`voz`, `teclado`, `boton`) la declara la interfaz. Sirve para
  diagnosticar, no como prueba: un cliente con ticket válido podría mentir. Lo
  que sí garantiza el núcleo es la sesión, el Origin y la autenticación.
- El HMAC permite comprobar si dos entradas fueron el mismo texto, o si un
  texto concreto llegó, sin guardar el contenido. Quien tenga la clave
  (`telemetria.key`, permisos 0600 en `<home>`) puede probar textos candidatos.
- El filtro de palabras sueltas no elimina falsos reconocimientos de dos o más
  palabras. Las acciones con efecto siguen detrás de la aprobación por acción,
  y un «sí» por voz no aprueba nada de riesgo alto.

## 9. Cómo investigar un caso parecido a partir de ahora

```powershell
# Entradas de la última hora: fuente, sesión, aceptada y motivo (sin texto)
Get-Content "$env:USERPROFILE\.crisvis\telemetria-entradas.jsonl" -Tail 50 |
  ForEach-Object { $_ | ConvertFrom-Json } |
  Select-Object ts, session_id, source_type, channel, accepted, reason, length
```

Cruzar `session_id` con `auditoria.jsonl` (acciones) y
`auditoria-adaptador.jsonl` (informes).
