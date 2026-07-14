import { useEffect, useState } from 'react'
import { api } from '../api'

// Franja pasiva de riesgo de helada para el lote activo. Sólo mira los campos
// de heladas del pronóstico GFS (helada_meteorologica/agrometeorologica) que
// ya calcula app/services/clima.py — no repite el resto de ForecastPanel
// (granizo/viento/lluvia/rayos), que quedó fuera de esta app de peritaje.
const NOCHES_VENTANA = 3

export default function HeladaAlertPanel({ active, lote }) {
  const [fc, setFc] = useState(null)
  const [err, setErr] = useState(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    setFc(null); setErr(null)
    if (active == null) return
    let cancel = false
    setLoading(true)
    const coords = lote ? { lat: lote.centroide_lat, lon: lote.centroide_lon } : null
    api.forecast(active, coords)
      .then((d) => !cancel && setFc(d))
      .catch((e) => !cancel && setErr(e.message))
      .finally(() => !cancel && setLoading(false))
    return () => { cancel = true }
  }, [active, lote])

  if (active == null) return null

  if (loading) {
    return (
      <div className="glass rounded-xl px-4 py-2.5 text-xs text-slate-400 flex items-center gap-2 shrink-0">
        <span className="w-3 h-3 rounded-full border-2 border-sky-400/30 border-t-sky-400 animate-spin shrink-0" />
        Consultando riesgo de helada…
      </div>
    )
  }

  if (err) {
    return (
      <div className="glass rounded-xl px-4 py-2.5 text-xs text-amber-300/80 shrink-0">
        ⚠️ No se pudo consultar el pronóstico de heladas ({err}).
      </div>
    )
  }

  if (!fc) return null

  const dias = (fc.dias || []).slice(0, NOCHES_VENTANA)
  const hoy = dias[0]
  const riesgoEsNoche = !!hoy?.helada_agrometeorologica
  const proxima = fc.resumen?.proxima_helada
  const proximaEnVentana = proxima && dias.some((d) => d.fecha === proxima)

  let estado = 'sin_riesgo'
  if (riesgoEsNoche) estado = 'esta_noche'
  else if (proximaEnVentana) estado = 'proxima'

  const ESTILOS = {
    esta_noche: 'bg-sky-500/15 border-sky-400/40 text-sky-200 animate-pulse',
    proxima: 'bg-sky-500/10 border-sky-400/25 text-sky-300',
    sin_riesgo: 'bg-emerald-500/10 border-emerald-500/25 text-emerald-300',
  }
  const MENSAJE = {
    esta_noche: `❄️ Riesgo de helada esta noche (mínima prevista ${hoy?.t_min ?? '—'}°C)`,
    proxima: `❄️ Próxima helada: ${proxima}`,
    sin_riesgo: `✓ Sin riesgo de helada en los próximos ${NOCHES_VENTANA} días`,
  }

  return (
    <div className={`glass rounded-xl px-4 py-2.5 flex items-center gap-3 flex-wrap text-xs border shrink-0 ${ESTILOS[estado]}`}>
      <span className="font-semibold">{MENSAJE[estado]}</span>
      <div className="flex gap-1.5 ml-auto">
        {dias.map((d) => (
          <div
            key={d.fecha}
            title={`${d.fecha}: mínima ${d.t_min ?? '—'}°C`}
            className={`rounded-md px-2 py-1 text-[10px] leading-tight text-center ${
              d.helada_agrometeorologica ? 'bg-sky-400/20 text-sky-200' : 'bg-white/5 text-slate-400'
            }`}
          >
            <div>{d.fecha.slice(5)}</div>
            <div className="font-bold">{d.t_min ?? '—'}°{d.helada_agrometeorologica ? ' ❄️' : ''}</div>
          </div>
        ))}
      </div>
    </div>
  )
}
