import { useEffect, useState } from 'react'
import { api, pollJob, ZONA_COLOR } from '../api'
import LoadingMessages from './LoadingMessages'

const SEV_CLR = {
  ok: 'bg-emerald-400/20 text-emerald-300', media: 'bg-amber-400/20 text-amber-300',
  alta: 'bg-rose-400/20 text-rose-300', baja: 'bg-sky-400/20 text-sky-300',
  'n/d': 'bg-slate-400/20 text-slate-300',
}
const NIVEL_CLR = { baja: 'text-emerald-400', media: 'text-amber-400', alta: 'text-rose-400' }
const fmt = (n) => '$' + Math.round(n).toLocaleString('es-AR')

export default function IAPanel({ active, onZonifDone }) {
  const [zonif, setZonif] = useState(null)
  const [rinde, setRinde] = useState(null)
  const [job, setJob] = useState(null)
  const [margen, setMargen] = useState(null)
  const [form, setForm] = useState({ rinde_objetivo: 4000, precio: 0.35, costo_base: 850 })

  useEffect(() => {
    setZonif(null); setRinde(null); setMargen(null); setJob(null)
    if (active == null) return
    let cancel = false
    api.rinde(active).then((d) => !cancel && setRinde(d)).catch(() => {})
    api.zonif(active).then((d) => !cancel && setZonif(d)).catch(() => {})
    return () => { cancel = true }
  }, [active])

  const ejecutarZonif = async () => {
    try {
      setZonif(null)
      const { job_id } = await api.lanzarZonif(active)
      const fin = await pollJob(job_id, (j) => setJob(j))
      setJob(null)
      if (fin.estado === 'COMPLETED') {
        setZonif(await api.zonif(active))
        onZonifDone?.()   // avisa a App para refrescar el overlay del mapa
      }
    } catch (e) {
      setJob({ estado: 'FAILED', error_msg: e.message })
    }
  }

  const calcularMargen = async () => {
    try { setMargen(await api.margen(active, form)) }
    catch (e) { setMargen({ error: e.message }) }
  }

  if (active == null)
    return <div className="h-full grid place-items-center text-slate-400 text-sm">Seleccioná un lote.</div>

  return (
    <div className="h-full grid grid-cols-1 lg:grid-cols-3 gap-4 p-4 overflow-y-auto animate-fadein">
      {/* Zonificación */}
      <div>
        <Sub>
          Zonificación KMeans
          <button
            onClick={ejecutarZonif}
            disabled={!!job}
            className="rounded-lg bg-emerald-400 text-emerald-950 px-2.5 py-1 text-xs font-bold disabled:opacity-50"
          >
            {zonif ? 'Recalcular' : 'Ejecutar'}
          </button>
        </Sub>
        {job ? (
          job.estado === 'FAILED'
            ? <p className="text-sm text-rose-300">Error: {job.error_msg}</p>
            : <div className="py-4"><LoadingMessages progreso={job.progreso} mensaje={job.mensaje} /></div>
        ) : zonif ? (
          <>
            <p className="text-xs text-slate-400 mb-1">Pico de vigor: {zonif.fecha_pico} · NDVI medio {zonif.ndvi_medio}</p>
            <p className="text-[11px] text-amber-300/80 mb-2">
              ⓘ Zonas calculadas con el NDVI del <b>{zonif.fecha_pico}</b> (máximo vigor), que puede diferir de la fecha del mapa base satelital.
            </p>
            <table className="w-full text-xs">
              <thead><tr className="text-slate-400">
                <th className="text-left py-1">Zona</th><th className="text-right">NDVI</th>
                <th className="text-right">ha</th><th className="text-right">%</th>
              </tr></thead>
              <tbody>
                {zonif.zonas.map((z) => (
                  <tr key={z.zona} className="border-t border-white/10">
                    <td className="py-1">
                      <span className="inline-block w-2.5 h-2.5 rounded-sm mr-1.5 align-middle"
                        style={{ background: ZONA_COLOR[z.etiqueta] || '#888' }} />
                      {z.etiqueta}
                    </td>
                    <td className="text-right">{z.ndvi_medio ?? '—'}</td>
                    <td className="text-right">{z.area_ha ?? '—'}</td>
                    <td className="text-right">{Math.round((z.frac || 0) * 100)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        ) : (
          <p className="text-sm text-slate-400">Sin zonificar. Clic en “Ejecutar” (descarga NDVI + KMeans).</p>
        )}
      </div>

      {/* Rinde */}
      <div>
        <Sub>Alerta de rinde</Sub>
        {!rinde ? <p className="text-sm text-slate-400">—</p> : (
          <>
            <div className={`text-2xl font-extrabold ${NIVEL_CLR[rinde.nivel_penalizacion]}`}>
              {rinde.nivel_penalizacion.toUpperCase()}
            </div>
            <p className="text-xs text-slate-400 mb-2">
              {rinde.penalizacion ? '⚠️ riesgo de penalización' : '✓ sin penalización significativa'} · score {rinde.score} · {rinde.modelo}
            </p>
            {rinde.factores.map((f, i) => (
              <div key={i} className="text-xs py-1.5 border-b border-white/10">
                <span className={`text-[10px] rounded px-1.5 py-0.5 mr-1.5 ${SEV_CLR[f.severidad] || ''}`}>{f.severidad}</span>
                {f.detalle}
              </div>
            ))}
          </>
        )}
      </div>

      {/* Margen bruto */}
      <div>
        <Sub>Margen bruto zonal</Sub>
        <div className="flex flex-col gap-2 mb-3">
          {[['rinde_objetivo', 'Rinde objetivo (kg/ha)', 1], ['precio', 'Precio ($/kg)', 0.01], ['costo_base', 'Costo base ($/ha)', 1]].map(([k, label, step]) => (
            <label key={k} className="text-[11px] text-slate-400 flex flex-col gap-0.5">
              {label}
              <input
                type="number" step={step} value={form[k]}
                onChange={(e) => setForm((f) => ({ ...f, [k]: parseFloat(e.target.value) }))}
                className="bg-black/25 border border-white/10 rounded-md px-2 py-1 text-sm text-slate-100"
              />
            </label>
          ))}
          <button onClick={calcularMargen} className="rounded-lg bg-emerald-400 text-emerald-950 px-2.5 py-1 text-xs font-bold">
            Calcular
          </button>
        </div>
        {margen?.error && <p className="text-sm text-amber-300">{margen.error}</p>}
        {margen && !margen.error && (
          <table className="w-full text-xs">
            <thead><tr className="text-slate-400">
              <th className="text-left py-1">Zona</th><th className="text-right">kg/ha</th>
              <th className="text-right">$/ha</th><th className="text-right">Margen</th>
            </tr></thead>
            <tbody>
              {margen.zonas.map((z) => (
                <tr key={z.etiqueta} className="border-t border-white/10">
                  <td className="py-1">
                    <span className="inline-block w-2.5 h-2.5 rounded-sm mr-1.5 align-middle"
                      style={{ background: ZONA_COLOR[z.etiqueta] || '#888' }} />
                    {z.etiqueta}
                  </td>
                  <td className="text-right">{z.rinde_kg_ha}</td>
                  <td className="text-right">{fmt(z.margen_ha)}</td>
                  <td className="text-right">{fmt(z.margen_bruto)}</td>
                </tr>
              ))}
              <tr className="border-t border-white/20 font-bold">
                <td className="py-1">TOTAL</td><td className="text-right">—</td>
                <td className="text-right">{fmt(margen.totales.margen_ha)}</td>
                <td className="text-right">{fmt(margen.totales.margen_bruto)}</td>
              </tr>
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

const Sub = ({ children }) => (
  <div className="text-xs uppercase tracking-wide text-slate-400 font-semibold mb-2 flex items-center justify-between">
    {children}
  </div>
)
