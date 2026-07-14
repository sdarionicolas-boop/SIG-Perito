import { useEffect, useState } from 'react'
import { api } from '../api'

const CULTIVOS = [
  ['mani', 'Maní'], ['trigo', 'Trigo'], ['cebada', 'Cebada'], ['colza', 'Colza'],
  ['maiz', 'Maíz'], ['maiz_tardio', 'Maíz tardío'], ['girasol', 'Girasol'],
  ['sorgo', 'Sorgo'], ['soja_1', 'Soja 1ra'], ['soja_2', 'Soja 2da'],
]
const ZONAS = [
  ['norte', 'Norte'], ['centro', 'Centro'], ['coeste', 'Centro-Oeste'],
  ['sudeste', 'Sudeste'], ['sudoeste', 'Sudoeste'],
]
// Las opciones deben CONTENER los keywords de etC (matching por substring).
const ETAPAS = [
  '', 'Siembra', 'Emergencia', 'Roseta', 'Macollaje', 'Encañazón', 'Espigazón',
  'Antesis', 'Floración', 'Grano lechoso', 'Madurez',
  'VE', 'V3', 'V4', 'V6', 'V8', 'V12', 'VT', 'Panojamiento', 'Clavado', 'Siliqua',
  'R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R7', 'R8',
]

function Select({ label, value, onChange, options }) {
  return (
    <label className="text-[11px] text-slate-400 flex flex-col gap-1">
      {label}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="bg-black/30 border border-white/10 rounded-md px-2 py-1.5 text-sm text-slate-100"
      >
        {options.map((o) => {
          const [v, t] = Array.isArray(o) ? o : [o, o || '— sin etapa —']
          return <option key={v} value={v}>{t}</option>
        })}
      </select>
    </label>
  )
}

export default function SanidadPanel({ active, lote }) {
  const [sel, setSel] = useState({ cultivo: '', zona: '', etapa: '' })
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [loading, setLoading] = useState(false)

  // Al cambiar de lote, resetear selectores (usar defaults del backend).
  useEffect(() => { setSel({ cultivo: '', zona: '', etapa: '' }) }, [active])

  useEffect(() => {
    if (active == null) { setData(null); return }
    let cancel = false
    setLoading(true); setErr(null)
    // Pasar coords para que el clima se consulte desde el navegador (bypass 429).
    const coords = lote ? { lat: lote.centroide_lat, lon: lote.centroide_lon } : null
    api.sanidad(active, { ...sel, coords })
      .then((d) => { if (!cancel) setData(d) })
      .catch((e) => { if (!cancel) setErr(e.message) })
      .finally(() => { if (!cancel) setLoading(false) })
    return () => { cancel = true }
  }, [active, sel, lote])

  if (active == null)
    return <div className="h-full grid place-items-center text-slate-400 text-sm">Seleccioná un lote.</div>

  const g = data?.alerta_global
  return (
    <div className="h-full flex flex-col p-4 overflow-y-auto animate-fadein">
      {/* Selectores + alerta global */}
      <div className="flex flex-wrap items-end gap-3 mb-3">
        <Select label="Cultivo" value={sel.cultivo || data?.cultivo || 'mani'}
          onChange={(v) => setSel((s) => ({ ...s, cultivo: v }))} options={CULTIVOS} />
        <Select label="Zona fitosanitaria" value={sel.zona || data?.zona || 'centro'}
          onChange={(v) => setSel((s) => ({ ...s, zona: v }))} options={ZONAS} />
        <Select label="Etapa fenológica" value={sel.etapa}
          onChange={(v) => setSel((s) => ({ ...s, etapa: v }))} options={ETAPAS} />
        {g && (
          <div className="ml-auto rounded-xl px-4 py-2 border" style={{ borderColor: g.color }}>
            <div className="text-[10px] uppercase tracking-wide text-slate-400">Alerta global</div>
            <div className="text-lg font-bold" style={{ color: g.color }}>
              {g.emoji} {g.nivel} · {g.score}
            </div>
            <div className="text-[11px] text-slate-400">{g.enfermedad}</div>
          </div>
        )}
      </div>

      {/* Resumen de clima usado */}
      {data?.clima && (
        <div className="text-[11px] text-slate-400 mb-3">
          Clima 15 d (Open-Meteo): T° {data.clima.tC}°C · Hum. suelo {data.clima.hVol}%vol ·
          Lluvia {data.clima.pMm} mm · NDVI {data.espectral?.ndvi ?? '—'} · NDWI {data.espectral?.ndwi ?? '—'}
        </div>
      )}
      {data?.clima_error && (
        <div className="text-[11px] text-amber-300 mb-3">Clima no disponible ({data.clima_error}); score sin componente climático.</div>
      )}

      {loading && <div className="text-xs text-slate-500 mb-2">Evaluando…</div>}
      {err && <div className="text-sm text-rose-300">{err}</div>}
      {data && !data.cultivo_disponible && (
        <div className="text-sm text-amber-300">Sin patógenos cargados para “{data.cultivo}”.</div>
      )}

      {/* Tarjetas por patógeno */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        {(data?.enfermedades || []).map((e) => (
          <div key={e.id} className="glass rounded-2xl p-3 border-l-4" style={{ borderColor: e.color }}>
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="font-semibold text-sm truncate">{e.nombre}</div>
                <div className="text-[11px] text-slate-400 italic truncate">{e.agente}</div>
              </div>
              <div className="text-right shrink-0">
                <div className="text-lg font-extrabold" style={{ color: e.color }}>{e.score}</div>
                <div className="text-[10px]" style={{ color: e.color }}>{e.emoji} {e.nivel}</div>
              </div>
            </div>
            <div className="mt-2 space-y-0.5">
              {e.factores.map((f, i) => (
                <div key={i} className={`text-[11px] ${f.ok ? 'text-emerald-300' : 'text-slate-500'}`}>
                  {f.ok ? '✓' : '✗'} {f.label}
                </div>
              ))}
            </div>
            <div className="mt-2 text-[11px] text-slate-300 border-t border-white/10 pt-2">
              <div><span className="text-slate-400">Tratamiento:</span> {e.tratamiento}</div>
              <div className="mt-1"><span className="text-slate-400">Prevención:</span> {e.medida}</div>
              <div className="mt-1 text-[10px] text-slate-500">Fuente: {e.fuente}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
