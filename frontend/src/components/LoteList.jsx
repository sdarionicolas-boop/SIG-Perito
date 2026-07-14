import { useState } from 'react'
import { COLORS } from '../api'

export default function LoteList({ lotes, selected, active, onToggle, drawing, setDrawing }) {
  const [selectedCampo, setSelectedCampo] = useState('')

  // Extraer los nombres de establecimientos/campos únicos
  const campos = Array.from(new Set(lotes.map((l) => l.campo).filter(Boolean))).sort()

  // Filtrar la lista local según la selección
  const filteredLotes = selectedCampo
    ? lotes.filter((l) => l.campo === selectedCampo)
    : lotes

  return (
    <div className="glass rounded-2xl h-full flex flex-col overflow-hidden">
      <div className="px-4 py-3 border-b border-white/10 flex items-center justify-between">
        <span className="text-xs uppercase tracking-wide text-slate-400 font-semibold">Lotes</span>
        <button
          onClick={() => setDrawing((d) => !d)}
          className={[
            'rounded-lg px-2.5 py-1 text-xs font-bold transition',
            drawing ? 'bg-rose-500 text-white' : 'bg-emerald-400 text-emerald-950 hover:brightness-110',
          ].join(' ')}
        >
          {drawing ? 'Cancelar dibujo' : '✏️ Dibujar lote'}
        </button>
      </div>

      {campos.length > 0 && (
        <div className="px-4 py-2 bg-white/5 border-b border-white/10 flex flex-col gap-1">
          <label className="text-[10px] uppercase tracking-wider text-slate-400 font-medium">
            Establecimiento / Grupo Demo:
          </label>
          <select
            value={selectedCampo}
            onChange={(e) => {
              const val = e.target.value
              setSelectedCampo(val)
              if (val) {
                const first = lotes.find((l) => l.campo === val)
                if (first) onToggle(first.id) // Enfocar y activar el primero al cambiar de grupo
              }
            }}
            className="w-full bg-black/40 border border-white/10 rounded px-2.5 py-1.5 text-xs text-slate-100 focus:outline-none focus:border-emerald-400/50"
          >
            <option value="">[ Mostrar Todos ]</option>
            {campos.map((c) => (
              <option key={c} value={c}>
                {c.replace(/^Demo:\s*/, '')}
              </option>
            ))}
          </select>
        </div>
      )}

      {drawing && (
        <div className="px-4 py-2 text-xs text-amber-300 bg-amber-400/10 border-b border-white/10">
          Hacé clic en el mapa para marcar los vértices. Cerrá con el botón “Guardar”.
        </div>
      )}

      <div className="overflow-y-auto flex-1">
        {filteredLotes.length === 0 && (
          <div className="p-4 text-sm text-slate-400">
            {lotes.length === 0 ? 'Cargando…' : 'Sin lotes en esta selección.'}
          </div>
        )}
        {filteredLotes.map((l) => {
          const idx = selected.indexOf(l.id)
          const isSel = idx >= 0
          const isActive = l.id === active
          return (
            <button
              key={l.id}
              onClick={() => onToggle(l.id)}
              className={[
                'w-full text-left px-4 py-2.5 border-b border-white/10 transition flex items-center gap-2',
                isActive ? 'bg-emerald-400/10' : 'hover:bg-sky-400/10',
              ].join(' ')}
            >
              <span
                className="w-2.5 h-2.5 rounded-sm shrink-0"
                style={{ background: isSel ? COLORS[idx % COLORS.length] : 'rgba(255,255,255,.15)' }}
              />
              <span className="min-w-0">
                <span className="block text-sm font-semibold truncate">
                  {l.nombre.replace(/^Lote_\d+_/, '')}
                </span>
                <span className="block text-xs text-slate-400">
                  {l.cultivo || '—'} · {l.area_ha ? `${l.area_ha} ha` : 's/área'}
                </span>
              </span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
