import { useEffect, useState } from 'react'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ReferenceLine, ResponsiveContainer,
} from 'recharts'
import { api } from '../api'
import LoadingMessages from './LoadingMessages'

const corto = (n) => (n || '').replace(/^Lote_\d+_/, '')

export default function CosechaPanel({ active }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    setData(null); setErr(null)
    if (active == null) return
    let cancel = false
    api.cosecha(active).then((d) => !cancel && setData(d)).catch((e) => !cancel && setErr(e.message))
    return () => { cancel = true }
  }, [active])

  if (active == null) return <Vacio>Seleccioná un lote para ver el avance de cosecha.</Vacio>
  if (err) return <Vacio>Error: {err}</Vacio>
  if (!data) return <div className="h-full grid place-items-center"><LoadingMessages msgs={['Cargando avance de cosecha…']} /></div>

  if (!data.disponible)
    return (
      <Vacio>
        <div className="text-center max-w-md p-6 glass rounded-2xl border border-white/10">
          <div className="text-3xl mb-2">🛰️</div>
          <p className="mb-2 text-slate-200 font-bold">Análisis de Cosecha por Radar</p>
          <p className="text-xs text-slate-400 leading-relaxed">
            El procesamiento del avance de trilla mediante radar de apertura sintética (Sentinel-1 SAR) se ejecuta de forma asincrónica debido a la alta demanda de cómputo geoespacial.
          </p>
          <p className="text-xs text-emerald-400/90 mt-3 font-medium">
            💡 En esta demo, la visualización está disponible para los lotes pre-procesados: <b>Lote Demo 1, 2 y 3</b> del grupo "QGIS Bonaerense".
          </p>
          <a
            href="https://www.linkedin.com/in/darionicolas/"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-block mt-4 px-4 py-2 rounded-lg bg-[#0A66C2]/20 border border-[#0A66C2]/40 text-[#70b5f9] text-xs font-semibold hover:bg-[#0A66C2]/30 transition-colors"
          >
            ¿Querés procesar tu lote? Contactame en LinkedIn →
          </a>
        </div>
      </Vacio>
    )

  const act = data.actual || {}
  return (
    <div className="h-full flex flex-col p-4 animate-fadein">
      <div className="text-xs uppercase tracking-wide text-slate-400 font-semibold mb-3">
        Avance de cosecha · {corto(data.nombre)} · {data.cultivo || '—'}
      </div>

      <div className="grid grid-cols-3 gap-2 mb-3">
        <KPI label="Cosechado" value={`${Math.round(act.pct ?? 0)}%`} cls="text-emerald-300" />
        <KPI label="Hectáreas" value={`${act.ha ?? '—'} / ${data.area_ha ?? '—'}`} sub="ha levantadas" />
        <KPI label="Inicio de cosecha" value={data.inicio || '—'} sub={`últ. paso ${act.fecha || '—'}`} />
      </div>

      <div className="flex-1 min-h-0">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data.serie} margin={{ top: 8, right: 16, bottom: 28, left: -8 }}>
            <defs>
              <linearGradient id="gcos" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#34d399" stopOpacity={0.5} />
                <stop offset="100%" stopColor="#34d399" stopOpacity={0.04} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="#ffffff14" />
            <XAxis dataKey="fecha" tick={{ fill: '#94a3b8', fontSize: 11 }} minTickGap={28} />
            <YAxis domain={[0, 100]} unit="%" tick={{ fill: '#94a3b8', fontSize: 11 }} />
            <Tooltip
              contentStyle={{ background: '#0b1220', border: '1px solid #ffffff22', borderRadius: 10 }}
              labelStyle={{ color: '#e5e7eb' }}
              formatter={(v) => [`${v}%`, 'Cosechado']}
            />
            {data.inicio && (
              <ReferenceLine
                x={data.inicio}
                stroke="#fbbf24"
                strokeDasharray="4 3"
                label={{ value: 'inicio', fill: '#fbbf24', fontSize: 10, position: 'insideTopRight' }}
              />
            )}
            <Area type="monotone" dataKey="pct" stroke="#34d399" strokeWidth={2} fill="url(#gcos)" />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

const Vacio = ({ children }) => (
  <div className="h-full grid place-items-center text-slate-400 text-sm p-4">{children}</div>
)
const KPI = ({ label, value, sub, cls }) => (
  <div className="rounded-xl bg-white/5 border border-white/10 p-3">
    <div className="text-[10px] uppercase tracking-wide text-slate-400">{label}</div>
    <div className={`text-xl font-extrabold ${cls || 'text-slate-100'}`}>{value}</div>
    {sub && <div className="text-[10px] text-slate-500">{sub}</div>}
  </div>
)
