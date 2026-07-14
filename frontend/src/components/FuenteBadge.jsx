// Badge de origen del dato de cobertura, compartido por Uso de Suelo y Certificación.
// real = MapBiomas precalculado · estimado = lote real sin dato (composición por
// defecto) · simulado = lote demo con historia ficticia.
const ESTILOS = {
  real: { cls: 'bg-sky-500/20 text-sky-300 border-sky-500/30', txt: 'Datos Reales · MapBiomas Arg. Col.2', pulse: false },
  estimado: { cls: 'bg-amber-500/20 text-amber-300 border-amber-500/30', txt: 'Estimación · sin dato MapBiomas', pulse: false },
  simulado: { cls: 'bg-rose-500/20 text-rose-300 border-rose-500/30', txt: 'Demo · Datos Simulados', pulse: true },
}

export default function FuenteBadge({ fuente }) {
  const e = ESTILOS[fuente] || ESTILOS.simulado
  return (
    <span className={`border text-[9px] px-1.5 py-0.5 rounded font-bold uppercase tracking-wider ${e.cls} ${e.pulse ? 'animate-pulse' : ''}`}>
      {e.txt}
    </span>
  )
}
