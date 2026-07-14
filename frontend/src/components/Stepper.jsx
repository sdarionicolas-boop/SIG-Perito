// Asistente agrícola: barra de progreso cliqueable. El paso actual se
// persiste en SessionStorage desde App para no perderlo al recargar.
export default function Stepper({ steps, step, setStep, disabled }) {
  return (
    <nav className="flex items-center gap-1 px-3 py-2 border-b border-white/10 overflow-x-auto">
      {steps.map((label, i) => {
        const estado = i === step ? 'activo' : i < step ? 'hecho' : 'pend'
        const bloqueado = disabled && i > 0
        return (
          <button
            key={label}
            disabled={bloqueado}
            onClick={() => !bloqueado && setStep(i)}
            className={[
              'flex items-center gap-1.5 rounded-full pl-1 pr-2.5 py-1 text-[13px] shrink-0 transition',
              estado === 'activo' ? 'bg-emerald-400 text-emerald-950 font-semibold' : '',
              estado === 'hecho' ? 'bg-emerald-400/15 text-emerald-300' : '',
              estado === 'pend' ? 'bg-white/5 text-slate-400' : '',
              bloqueado ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer hover:brightness-110',
            ].join(' ')}
          >
            <span className="grid place-items-center w-[18px] h-[18px] rounded-full bg-black/20 text-[10px] shrink-0">
              {i < step ? '✓' : i + 1}
            </span>
            <span className="whitespace-nowrap">{label}</span>
          </button>
        )
      })}
      {disabled && (
        <span className="ml-2 text-xs text-slate-500 shrink-0 whitespace-nowrap">Seleccioná un lote para avanzar</span>
      )}
    </nav>
  )
}
