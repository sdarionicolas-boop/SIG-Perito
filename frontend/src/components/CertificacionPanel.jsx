// frontend/src/components/CertificacionPanel.jsx
import { useEffect, useState } from 'react'
import {
  ComposedChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts'
import { api } from '../api'
import FuenteBadge from './FuenteBadge'

// Las 5 normativas del pliego real (orden cronológico por fecha de corte).
const NORMAS = [
  { key: 'RFS2_2007', sigla: 'RFS2', org: 'EPA · EE.UU.', corte: '19/12/2007' },
  { key: '2BSvs_2008', sigla: '2BSvs', org: 'Biocombustibles UE', corte: '01/01/2008' },
  { key: 'RTRS_2016', sigla: 'RTRS', org: 'Soja Responsable', corte: '03/06/2016' },
  { key: 'EUDR_2020', sigla: 'EUDR', org: 'Unión Europea', corte: '31/12/2020' },
  { key: 'CFR_2020', sigla: 'CFR', org: 'Canadá · LUB', corte: '01/07/2020' },
]

export default function CertificacionPanel({ active }) {
  const [data, setData] = useState(null)
  const [desvio, setDesvio] = useState(null)
  const [cargando, setCargando] = useState(false)
  const [error, setError] = useState(null)
  const [copiado, setCopiado] = useState(false)

  useEffect(() => {
    if (active == null) {
      setData(null)
      setDesvio(null)
      return
    }
    setCargando(true)
    setError(null)
    setDesvio(null)
    Promise.all([api.compliance(active), api.desvioNdvi(active).catch(() => null)])
      .then(([res, dsv]) => {
        setData(res)
        setDesvio(dsv)
        setCargando(false)
      })
      .catch((e) => {
        setError(e.message)
        setCargando(false)
      })
  }, [active])

  const copiarAlPortapapeles = () => {
    if (!data?.hash) return
    navigator.clipboard.writeText(data.hash)
    setCopiado(true)
    setTimeout(() => setCopiado(false), 2000)
  }

  if (active == null) {
    return (
      <div className="h-full flex items-center justify-center p-8 text-center text-slate-400">
        <p className="text-sm">Seleccioná un lote para evaluar el cumplimiento regulatorio de deforestación.</p>
      </div>
    )
  }

  if (cargando) {
    return (
      <div className="h-full flex items-center justify-center p-8 text-slate-400">
        <p className="text-sm">Evaluando reglas de cumplimiento y firmas criptográficas...</p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="h-full flex items-center justify-center p-8 text-rose-400">
        <p className="text-sm">Error en la evaluación: {error}</p>
      </div>
    )
  }

  const checks = data?.canonical?.checks_compliance || {}
  const eudrDetalle = data?.canonical?.eudr_detalle || null
  const dosbsDetalle = data?.canonical?.dosbsvs_detalle || null
  const hash = data?.hash || ""
  const urlPdf = `/api/compliance/${active}/pdf`

  return (
    <div className="h-full flex flex-col p-4 overflow-y-auto animate-fadein">
      {/* Cabecera con advertencia visible de simulación */}
      <div className="flex items-center justify-between border-b border-white/10 pb-2 mb-3">
        <div className="flex items-center gap-2">
          <span className="text-xs uppercase tracking-wide text-slate-400 font-semibold">
            Certificación Ambiental &amp; Auditoría Criptográfica
          </span>
          <FuenteBadge fuente={data?.fuente} />
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Lado Izquierdo: Lista de Checks de cumplimiento (2 columnas en lg) */}
        <div className="lg:col-span-2 flex flex-col gap-3">
          <span className="text-xs text-slate-300 font-bold">Estado de Normas Internacionales:</span>
          
          <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-3">
            {NORMAS.map((n) => (
              <NormaCard key={n.key} norma={n} estado={checks[n.key]} />
            ))}
          </div>

          {/* Detalle EUDR: crop/noncrop + análisis de dos períodos */}
          {eudrDetalle && <DetalleEudr det={eudrDetalle} />}

          {/* Detalle 2BSvs: área elegible */}
          {dosbsDetalle && <Detalle2Bsvs det={dosbsDetalle} />}

          {/* Botón para descargar el PDF */}
          <div className="mt-2">
            <a 
              href={urlPdf} 
              download
              className="inline-flex items-center gap-2 bg-emerald-500 hover:bg-emerald-400 text-slate-950 text-xs font-bold px-4 py-2 rounded-xl transition duration-200"
            >
              <span>📄</span> Descargar Reporte PDF Auditado
            </a>
            <span className="text-[10px] text-slate-500 ml-3 font-semibold block sm:inline mt-1 sm:mt-0">
              * El documento PDF incluirá marcas de agua de Simulación y el Hash firmado.
            </span>
          </div>
        </div>

        {/* Lado Derecho: Bloque de Criptografía Blockchain */}
        <div className="flex flex-col bg-white/5 border border-white/10 rounded-xl p-3 justify-between">
          <div>
            <span className="text-xs text-slate-300 font-bold block mb-1">Evidencia Digital de Auditoría</span>
            <p className="text-[10px] text-slate-400 mb-3 leading-relaxed">
              El hash SHA-256 se genera a partir de la estructura canónica de datos del lote (nombre, coordenadas, superficie y estados de compliance). Garantiza que la evidencia sea inalterable.
            </p>
            
            <div className="bg-black/40 border border-white/10 rounded-lg p-2 font-mono text-[9px] text-slate-200 break-all select-all mb-2">
              {hash}
            </div>
          </div>

          <button
            onClick={copiarAlPortapapeles}
            className={`w-full py-1.5 rounded-lg text-xs font-bold transition duration-200 ${
              copiado
                ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
                : 'bg-white/10 hover:bg-white/15 text-slate-200 border border-white/10'
            }`}
          >
            {copiado ? '✓ ¡Copiado!' : '📋 Copiar Hash SHA-256'}
          </button>
        </div>
      </div>

      {/* Monitoreo de rendimiento: desvío de NDVI vs cohorte regional (normativa 5267) */}
      <DesvioSection desvio={desvio} />
    </div>
  )
}

const fmtPct = (v) => (v == null ? '—' : `${v > 0 ? '+' : ''}${v}%`)

function DesvioSection({ desvio }) {
  return (
    <div className="mt-4 border-t border-white/10 pt-3">
      <div className="flex items-center gap-2 mb-3">
        <span className="text-xs uppercase tracking-wide text-slate-400 font-semibold">
          Monitoreo de Rendimiento · Desvío NDVI vs. región
        </span>
        <span className="bg-sky-500/20 text-sky-300 border border-sky-500/30 text-[9px] px-1.5 py-0.5 rounded font-bold uppercase tracking-wider">
          Datos Reales · Normativa 5267
        </span>
      </div>

      {!desvio || !desvio.disponible ? (
        <p className="text-xs text-slate-500 py-4">
          Sin serie NDVI suficiente para este lote (se necesita un cohorte del mismo cultivo).
        </p>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {/* KPIs */}
          <div className="flex flex-col gap-2">
            <div className={`rounded-xl p-3 border ${
              desvio.estado === 'ALERTA'
                ? 'bg-rose-500/10 border-rose-500/30'
                : 'bg-emerald-500/10 border-emerald-500/25'
            }`}>
              <div className="text-[10px] uppercase tracking-wide text-slate-400">Estado actual</div>
              <div className={`text-xl font-extrabold ${
                desvio.estado === 'ALERTA' ? 'text-rose-300' : 'text-emerald-300'
              }`}>
                {desvio.estado === 'ALERTA' ? '⚠ ALERTA' : '✓ EN LÍNEA'}
              </div>
              <div className="text-[10px] text-slate-500">
                últ. desvío {fmtPct(desvio.actual?.desvio_pct)} · {desvio.actual?.fecha || '—'}
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div className="rounded-xl bg-white/5 border border-white/10 p-2">
                <div className="text-[10px] uppercase text-slate-400">Alertas</div>
                <div className="text-lg font-bold text-slate-100">{desvio.n_alertas}</div>
              </div>
              <div className="rounded-xl bg-white/5 border border-white/10 p-2">
                <div className="text-[10px] uppercase text-slate-400">Peor desvío</div>
                <div className="text-lg font-bold text-rose-300">{fmtPct(desvio.desvio_peor)}</div>
              </div>
            </div>
            <div className="text-[10px] text-slate-500 leading-relaxed">
              Esperado = mediana del cohorte de <b>{desvio.n_cohorte_lotes}</b> lotes de{' '}
              <b>{desvio.cultivo}</b>. Alerta si el lote cae ≥{desvio.umbral_pct}% bajo la región
              en ventana productiva.
            </div>
          </div>

          {/* Gráfico NDVI lote vs esperado */}
          <div className="lg:col-span-2 h-[200px]">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={desvio.serie} margin={{ top: 8, right: 12, bottom: 4, left: -12 }}>
                <CartesianGrid stroke="#ffffff14" />
                <XAxis dataKey="fecha" tick={{ fill: '#94a3b8', fontSize: 10 }} minTickGap={40} />
                <YAxis domain={[0, 1]} tick={{ fill: '#94a3b8', fontSize: 10 }} />
                <Tooltip
                  contentStyle={{ background: '#0b1220', border: '1px solid #ffffff22', borderRadius: 10 }}
                  labelStyle={{ color: '#e5e7eb' }}
                  formatter={(v, n) => [v, n === 'ndvi' ? 'NDVI lote' : 'Esperado región']}
                />
                <Line type="monotone" dataKey="esperado" stroke="#64748b" strokeWidth={1.5}
                      strokeDasharray="4 3" dot={false} isAnimationActive={false} />
                <Line type="monotone" dataKey="ndvi" stroke="#34d399" strokeWidth={2}
                      isAnimationActive={false}
                      dot={(p) => {
                        const { cx, cy, payload } = p
                        if (payload.alerta)
                          return <circle key={payload.fecha} cx={cx} cy={cy} r={3.5} fill="#f43f5e" stroke="#0b1220" strokeWidth={1} />
                        return <circle key={payload.fecha} cx={cx} cy={cy} r={0} fill="none" />
                      }} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
    </div>
  )
}

function NormaCard({ norma, estado }) {
  return (
    <div className="bg-white/5 border border-white/10 rounded-xl p-3 flex flex-col justify-between">
      <div>
        <div className="text-[11px] uppercase text-slate-200 font-bold">{norma.sigla}</div>
        <div className="text-[10px] text-slate-400">{norma.org}</div>
        <div className="text-[9px] text-slate-500 mt-1">Corte: {norma.corte}</div>
      </div>
      <div className="mt-3">
        <EstadoBadge estado={estado} />
      </div>
    </div>
  )
}

function EstadoBadge({ estado }) {
  if (estado === "Aprobado")
    return (
      <span className="text-xs font-bold text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 px-2 py-1 rounded block text-center">
        ✓ CONFORME
      </span>
    )
  if (estado?.startsWith("Aprobado con salvedad"))
    return (
      <span className="text-xs font-bold text-amber-400 bg-amber-500/10 border border-amber-500/20 px-2 py-1 rounded block text-center">
        ⚠ CON SALVEDAD
      </span>
    )
  return (
    <span className="text-xs font-bold text-rose-400 bg-rose-500/10 border border-rose-500/20 px-2 py-1 rounded block text-center">
      ✗ NO CONFORME
    </span>
  )
}

function DetalleEudr({ det }) {
  const { uso_suelo_actual: uso, periodo_pre_2020: pre, periodo_post_2020: post } = det
  return (
    <div className="bg-white/5 border border-white/10 rounded-xl p-3">
      <span className="text-xs text-slate-300 font-bold block mb-2">
        Detalle EUDR · Discriminación crop/noncrop + dos períodos
      </span>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 text-[11px]">
        <div className="rounded-lg bg-black/20 p-2">
          <div className="text-slate-400 uppercase text-[9px] mb-1">Uso de suelo actual</div>
          <div className="text-slate-200">{uso.crop_ha} ha agrícola (crop)</div>
          <div className="text-slate-400">{uso.noncrop_ha} ha no agrícola (noncrop)</div>
        </div>
        <div className="rounded-lg bg-black/20 p-2">
          <div className="text-slate-400 uppercase text-[9px] mb-1">Pre-2020 (2008-2020)</div>
          <div className={pre.desmonte_detectado ? "text-amber-300" : "text-emerald-300"}>
            {pre.desmonte_detectado ? `Desmonte en ${pre.anio_estimado}` : "Sin desmonte detectado"}
          </div>
          <div className="text-slate-500 text-[10px] mt-1">{pre.categoria_otbn}</div>
        </div>
        <div className="rounded-lg bg-black/20 p-2">
          <div className="text-slate-400 uppercase text-[9px] mb-1">Post-2020 (tolerancia cero)</div>
          <div className={post.cumple ? "text-emerald-300" : "text-rose-300"}>
            {post.cumple ? "Sin pérdida de bosque" : `Pérdida: ${post.perdida_bosque_ha} ha`}
          </div>
        </div>
      </div>
    </div>
  )
}

function Detalle2Bsvs({ det }) {
  const elegiblePleno = det.pct_elegible >= 99.9
  return (
    <div className="bg-white/5 border border-white/10 rounded-xl p-3">
      <span className="text-xs text-slate-300 font-bold block mb-2">
        Detalle 2BSvs · Área elegible (corte 2008)
      </span>
      <div className="flex items-center gap-3 mb-2">
        <div className="flex-1 h-3 rounded-full bg-black/30 overflow-hidden">
          <div
            className={elegiblePleno ? "h-full bg-emerald-400" : "h-full bg-amber-400"}
            style={{ width: `${det.pct_elegible}%` }}
          />
        </div>
        <span className={`text-sm font-extrabold ${elegiblePleno ? "text-emerald-300" : "text-amber-300"}`}>
          {det.pct_elegible}% elegible
        </span>
      </div>
      <div className="text-[11px] text-slate-400 leading-relaxed">
        {det.area_agricola_ha} ha agrícola
        {det.deforestacion_post_2008_ha > 0 && <> · −{det.deforestacion_post_2008_ha} ha deforestación</>}
        {det.conversion_pastura_post_2008_ha > 0 && <> · −{det.conversion_pastura_post_2008_ha} ha conversión pastizal/pastura</>}
        {' '}= <b className="text-slate-200">{det.area_elegible_ha} ha elegibles</b>
      </div>
      <div className="text-[10px] text-slate-500 mt-1">⚠ {det.biodiversidad}</div>
    </div>
  )
}
