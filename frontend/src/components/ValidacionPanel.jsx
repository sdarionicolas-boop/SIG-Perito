import { useEffect, useState } from 'react'
import { api } from '../api'

const NIVEL_CLR = { alta: 'text-emerald-400', media: 'text-amber-400', baja: 'text-rose-400' }

function Metric({ label, value }) {
  return (
    <div className="bg-white/5 border border-white/10 rounded-lg px-3 py-2">
      <div className="text-[10px] uppercase tracking-wide text-slate-400">{label}</div>
      <div className="text-sm font-bold text-slate-100">{value}</div>
    </div>
  )
}

export default function ValidacionPanel({ active }) {
  const [val, setVal] = useState(null)
  const [met, setMet] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    setVal(null); setErr(null)
    if (active != null) {
      api.validacion(active).then(setVal).catch((e) => setErr(e.message))
    }
    api.metricas().then(setMet).catch(() => {})
  }, [active])

  return (
    <div className="h-full grid grid-cols-1 lg:grid-cols-3 gap-4 p-4 overflow-y-auto animate-fadein">
      {/* Consistencia del lote activo */}
      <div className="lg:col-span-2">
        <Sub>Consistencia temporal del lote activo</Sub>
        {active == null ? <P>Seleccioná un lote.</P> : err ? <P>{err}</P> : !val ? <P>Cargando…</P> : (
          <>
            <div className="flex items-baseline gap-3 mb-3">
              <span className={`text-3xl font-extrabold ${NIVEL_CLR[val.consistencia.nivel]}`}>
                {val.consistencia.score}
              </span>
              <span className="text-sm text-slate-400">/ 100 · consistencia {val.consistencia.nivel}</span>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 mb-3">
              <Metric label="Observaciones" value={val.muestreo.observaciones} />
              <Metric label="Cadencia media" value={`${val.muestreo.cadencia_media_dias} d`} />
              <Metric label="Gap máximo" value={`${val.muestreo.gap_max_dias} d`} />
              <Metric label="NDVI fuera rango" value={val.rango.ndvi_fuera_de_rango} />
              <Metric label="Spikes (ruido)" value={`${val.ruido.spike_pct}%`} />
              <Metric label="Pico NDVI" value={`${val.fenologia.ndvi_pico} · ${val.fenologia.fecha_pico.slice(5)}`} />
              <Metric label="Curva unimodal" value={val.fenologia.unimodal ? 'Sí' : 'No'} />
              <Metric label="Sensores" value={Object.entries(val.sensores).map(([k, v]) => `${k.includes('andsat') ? 'L8' : k.includes('entinel-2') ? 'S2' : k.includes('entinel-1') ? 'S1' : k}:${v}`).join(' ')} />
              <Metric label="Cosecha SAR" value={val.coherencia_radar.fecha_cosecha_fisica || '—'} />
            </div>
            {val.consistencia.avisos.length ? (
              <ul className="text-xs text-amber-300 space-y-1">
                {val.consistencia.avisos.map((a, i) => <li key={i}>⚠️ {a}</li>)}
              </ul>
            ) : <p className="text-xs text-emerald-300">✓ Sin advertencias: serie internamente consistente.</p>}
          </>
        )}
      </div>

      {/* Métricas operativas */}
      <div>
        <Sub>Operación · latencia &amp; Processing Units</Sub>
        {!met ? <P>—</P> : (
          <>
            <div className="text-[11px] uppercase tracking-wide text-slate-400 mb-1">Consumo CDSE (estimado)</div>
            <div className="grid grid-cols-2 gap-2 mb-3">
              <Metric label="Llamadas API" value={met.uso_api.total_llamadas} />
              <Metric label="PU estimadas" value={met.uso_api.pu_estimadas} />
              <Metric label="Presupuesto" value={met.uso_api.presupuesto_pu} />
              <Metric label="% usado" value={`${met.uso_api.pct_presupuesto ?? 0}%`} />
            </div>
            <div className="text-[11px] uppercase tracking-wide text-slate-400 mb-1">Latencia por endpoint (ms)</div>
            <table className="w-full text-[11px]">
              <thead><tr className="text-slate-400"><th className="text-left py-1">Ruta</th><th className="text-right">avg</th><th className="text-right">p95</th></tr></thead>
              <tbody>
                {Object.entries(met.latencia).slice(0, 8).map(([ruta, m]) => (
                  <tr key={ruta} className="border-t border-white/10">
                    <td className="py-1 truncate max-w-[160px]" title={ruta}>{ruta.replace('GET ', '').replace('/api/', '')}</td>
                    <td className="text-right">{m.avg_ms}</td>
                    <td className="text-right">{m.p95_ms}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>
    </div>
  )
}

const Sub = ({ children }) => (
  <div className="text-xs uppercase tracking-wide text-slate-400 font-semibold mb-2">{children}</div>
)
const P = ({ children }) => <p className="text-sm text-slate-400">{children}</p>
