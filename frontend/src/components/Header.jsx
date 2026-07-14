import { useEffect } from 'react'

function Kpi({ label, value }) {
  return (
    <div className="glass rounded-xl px-4 py-2 min-w-[110px]">
      <div className="text-[11px] uppercase tracking-wide text-slate-400">{label}</div>
      <div className="text-lg font-bold text-slate-100 truncate">{value}</div>
    </div>
  )
}

export default function Header({ nLotes, supTotal, activo }) {
  const isHF = typeof window !== 'undefined' && (
    window.location.hostname.includes('hf.space') || window.location.hostname.includes('huggingface')
  )
  const title = isHF ? "🛰️ Peritaje Satelital" : "🛰️ Peritaje Satelital de Eventualidades"

  useEffect(() => {
    document.title = isHF ? "Peritaje Satelital" : "Peritaje de Eventualidades"
  }, [isHF])

  return (
    <header className="glass sticky top-0 z-[1000] flex items-center gap-4 px-6 py-3 border-b border-white/10">
      <div>
        <h1 className="text-lg font-semibold m-0">{title}</h1>
        <div className="text-xs text-slate-400">Detección y reporte de siniestros satelitales · CDSE / Open-Meteo</div>
      </div>
      <div className="ml-auto flex gap-2.5">
        <Kpi label="Lotes" value={nLotes} />
        <Kpi label="Sup. total" value={`${supTotal.toLocaleString('es-AR', { maximumFractionDigits: 0 })} ha`} />
        <Kpi label="Lote activo" value={activo ? activo.nombre.replace(/^Lote_\d+_/, '') : '—'} />
      </div>
    </header>
  )
}
