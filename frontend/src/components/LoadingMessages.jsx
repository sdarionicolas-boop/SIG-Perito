import { useEffect, useState } from 'react'

const DEFAULT_MSGS = [
  'Contactando satélites Sentinel…',
  'Descargando reflectancias…',
  'Procesando índices de vegetación…',
  'Afinando los píxeles…',
  'Casi listo…',
]

// Mensajes de carga rotativos. Si se pasa `progreso` y `mensaje` (de un job),
// los muestra; si no, rota mensajes genéricos animados.
export default function LoadingMessages({ progreso, mensaje, msgs = DEFAULT_MSGS }) {
  const [i, setI] = useState(0)
  useEffect(() => {
    const t = setInterval(() => setI((x) => (x + 1) % msgs.length), 2200)
    return () => clearInterval(t)
  }, [msgs.length])

  return (
    <div className="flex flex-col items-center gap-3 text-center animate-fadein">
      <div className="w-8 h-8 rounded-full border-2 border-emerald-400/30 border-t-emerald-400 animate-spin" />
      <div className="text-sm text-slate-300">{mensaje || msgs[i]}</div>
      {typeof progreso === 'number' && (
        <div className="w-56 h-1.5 rounded-full bg-white/10 overflow-hidden">
          <div className="h-full bg-emerald-400 transition-all" style={{ width: `${progreso}%` }} />
        </div>
      )}
    </div>
  )
}
