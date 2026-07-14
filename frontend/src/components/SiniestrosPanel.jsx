import { useEffect, useState } from 'react'
import { api } from '../api'

const EVENTO_LABEL = {
  granizo: '🧊 Granizo', helada: '❄️ Helada', viento: '🌬️ Viento',
  sequia: '🌵 Sequía', inundacion: '💧 Inundación',
}

// Listado/búsqueda de siniestros (casos de peritaje con metadatos de póliza).
// Complementa a PeritajePanel: ahí se carga UN caso a la vez; acá se buscan
// TODOS los casos registrados, por aseguradora o número de póliza.
export default function SiniestrosPanel({ onVerCaso }) {
  const [filtros, setFiltros] = useState({ aseguradora: '', numero_poliza: '' })
  const [casos, setCasos] = useState(null)
  const [err, setErr] = useState(null)
  const [loading, setLoading] = useState(false)

  const buscar = async () => {
    setLoading(true); setErr(null)
    try {
      setCasos(await api.siniestros(filtros))
    } catch (e) {
      setErr(e.message)
    } finally {
      setLoading(false)
    }
  }

  // Carga inicial (sin filtros) al montar el panel.
  useEffect(() => { buscar() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="h-full flex flex-col p-4 overflow-y-auto animate-fadein">
      <div className="text-xs uppercase tracking-wide text-slate-400 font-semibold mb-2">
        Casos registrados (siniestros con datos de póliza)
      </div>

      <div className="flex flex-wrap items-end gap-3 mb-3 shrink-0">
        <label className="text-[11px] text-slate-400 flex flex-col gap-0.5">
          Aseguradora
          <input
            type="text" value={filtros.aseguradora} placeholder="Buscar por aseguradora…"
            onChange={(e) => setFiltros((f) => ({ ...f, aseguradora: e.target.value }))}
            onKeyDown={(e) => e.key === 'Enter' && buscar()}
            className="bg-black/25 border border-white/10 rounded-md px-2 py-1 text-sm text-slate-100 w-48"
          />
        </label>
        <label className="text-[11px] text-slate-400 flex flex-col gap-0.5">
          N° de póliza
          <input
            type="text" value={filtros.numero_poliza} placeholder="Buscar por póliza…"
            onChange={(e) => setFiltros((f) => ({ ...f, numero_poliza: e.target.value }))}
            onKeyDown={(e) => e.key === 'Enter' && buscar()}
            className="bg-black/25 border border-white/10 rounded-md px-2 py-1 text-sm text-slate-100 w-40"
          />
        </label>
        <button
          onClick={buscar} disabled={loading}
          className="rounded-lg bg-emerald-400 text-emerald-950 px-3 py-1.5 text-xs font-bold disabled:opacity-50 hover:brightness-110 transition"
        >
          Buscar
        </button>
      </div>

      {loading && <p className="text-sm text-slate-400">Buscando…</p>}
      {err && <div className="text-sm text-rose-300 bg-rose-500/10 border border-rose-500/25 rounded-lg px-3 py-2 mb-3">{err}</div>}

      {casos && !loading && casos.length === 0 && (
        <p className="text-sm text-slate-400">
          Sin casos registrados{filtros.aseguradora || filtros.numero_poliza
            ? ' con ese filtro.'
            : ' todavía. Cargá los datos del siniestro (aseguradora/póliza) al peritar un lote para que aparezca acá.'}
        </p>
      )}

      {casos && casos.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-slate-400 text-left border-b border-white/10">
                <th className="py-1.5 pr-3 font-medium">Fecha evento</th>
                <th className="py-1.5 pr-3 font-medium">Tipo</th>
                <th className="py-1.5 pr-3 font-medium">Lote</th>
                <th className="py-1.5 pr-3 font-medium">Aseguradora</th>
                <th className="py-1.5 pr-3 font-medium">N° Póliza</th>
                <th className="py-1.5 pr-3 font-medium">Productor</th>
                <th className="py-1.5 pr-3 font-medium">Actualizado</th>
                <th className="py-1.5 font-medium"></th>
              </tr>
            </thead>
            <tbody>
              {casos.map((c) => (
                <tr key={c.id} className="border-b border-white/5 hover:bg-white/5 transition">
                  <td className="py-1.5 pr-3">{c.fecha_evento}</td>
                  <td className="py-1.5 pr-3 whitespace-nowrap">{EVENTO_LABEL[c.tipo_evento] || c.tipo_evento}</td>
                  <td className="py-1.5 pr-3">
                    {c.lote_nombre} <span className="text-slate-500">({c.lote_cultivo})</span>
                  </td>
                  <td className="py-1.5 pr-3">{c.aseguradora || '—'}</td>
                  <td className="py-1.5 pr-3">{c.numero_poliza || '—'}</td>
                  <td className="py-1.5 pr-3">{c.productor || '—'}</td>
                  <td className="py-1.5 pr-3 text-slate-500 whitespace-nowrap">{c.updated_at?.slice(0, 16)}</td>
                  <td className="py-1.5">
                    <button
                      onClick={() => onVerCaso?.(c.lote_id)}
                      className="text-sky-300 bg-sky-400/10 border border-sky-400/25 rounded-md px-2 py-1 hover:bg-sky-400/20 transition whitespace-nowrap"
                    >
                      Ver →
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
