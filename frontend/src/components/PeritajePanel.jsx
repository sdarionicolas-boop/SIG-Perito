import { useEffect, useState } from 'react'
import { api, pollJob } from '../api'
import LoadingMessages from './LoadingMessages'

// Tipos de evento soportados por el backend (extractor/peritaje_eventos.py).
const EVENTOS = [
  { v: 'granizo', label: '🧊 Granizo', color: '#4A90D9' },
  { v: 'helada', label: '❄️ Helada', color: '#8E44AD' },
  { v: 'viento', label: '🌬️ Viento', color: '#27AE60' },
  { v: 'sequia', label: '🌵 Sequía', color: '#E67E22' },
  { v: 'inundacion', label: '💧 Inundación', color: '#2980B9' },
]
const COLOR_EVENTO = Object.fromEntries(EVENTOS.map((e) => [e.v, e.color]))

// Colores de las barras de severidad (coinciden con el reporte HTML del backend).
const SEV = {
  severa: { c: '#8B0000', t: 'Severo' },
  moderada: { c: '#FF8C00', t: 'Moderado' },
  leve: { c: '#FFD700', t: 'Leve' },
  sin_dano: { c: '#4CAF50', t: 'Sin daño' },
}

export default function PeritajePanel({ active, lote, onPeritajeDone }) {
  const [form, setForm] = useState({
    fecha_evento: '', tipo_evento: 'granizo', ventana_dias: 14, baseline_anos: 3,
    aseguradora: '', numero_poliza: '', productor: '', comentarios_perito: '',
  })
  const [res, setRes] = useState(null)
  const [job, setJob] = useState(null)
  const [err, setErr] = useState(null)
  const [showSiniestro, setShowSiniestro] = useState(false)

  // Al cambiar de lote: limpiar y buscar si ya hay un peritaje previo.
  useEffect(() => {
    setRes(null); setJob(null); setErr(null)
    if (active == null) return
    let cancel = false
    api.peritaje(active).then((d) => !cancel && setRes(d)).catch(() => {})
    return () => { cancel = true }
  }, [active])

  const lanzar = async () => {
    if (!form.fecha_evento) { setErr('Indicá la fecha del evento.'); return }
    setErr(null); setRes(null)
    try {
      const { job_id } = await api.lanzarPeritaje(active, form)
      // El peritaje baja varios composites de CDSE: puede tardar minutos.
      const fin = await pollJob(job_id, (j) => setJob(j), 3000, 120)
      setJob(null)
      if (fin.estado === 'COMPLETED') {
        setRes(await api.peritaje(active))
        onPeritajeDone?.()   // avisa a App para refrescar el overlay de severidad en el mapa
      } else {
        setErr(fin.error_msg || 'El peritaje falló.')
      }
    } catch (e) {
      setJob(null); setErr(e.message)
    }
  }

  if (active == null)
    return <Vacio>Seleccioná un lote para peritar un evento.</Vacio>

  return (
    <div className="h-full flex flex-col p-4 overflow-y-auto animate-fadein">
      <div className="text-xs uppercase tracking-wide text-slate-400 font-semibold mb-2">
        Peritaje de eventualidades · Sentinel-2 (CDSE, sin GEE)
      </div>

      {/* Formulario */}
      <div className="flex flex-wrap items-end gap-3 mb-3 shrink-0">
        <label className="text-[11px] text-slate-400 flex flex-col gap-0.5">
          Fecha del evento
          <input
            type="date" value={form.fecha_evento}
            title="Para granizo/viento nocturno: declará el DÍA DESPUÉS de la noche de tormenta (ej. si la tormenta fue la noche del 18 al 19, declará 19). El escaneo satelital GOES busca la noche anterior a esta fecha; NDVI/ERA5 son más tolerantes al día exacto."
            onChange={(e) => setForm((f) => ({ ...f, fecha_evento: e.target.value }))}
            className="bg-black/25 border border-white/10 rounded-md px-2 py-1 text-sm text-slate-100"
          />
        </label>
        <label className="text-[11px] text-slate-400 flex flex-col gap-0.5">
          Tipo de evento
          <select
            value={form.tipo_evento}
            onChange={(e) => setForm((f) => ({ ...f, tipo_evento: e.target.value }))}
            className="bg-black/25 border border-white/10 rounded-md px-2 py-1 text-sm text-slate-100"
          >
            {EVENTOS.map((e) => <option key={e.v} value={e.v}>{e.label}</option>)}
          </select>
        </label>
        <label className="text-[11px] text-slate-400 flex flex-col gap-0.5">
          Ventana (días ±)
          <input
            type="number" min={3} max={60} value={form.ventana_dias}
            onChange={(e) => setForm((f) => ({ ...f, ventana_dias: parseInt(e.target.value) || 14 }))}
            className="bg-black/25 border border-white/10 rounded-md px-2 py-1 text-sm text-slate-100 w-20"
          />
        </label>
        <label className="text-[11px] text-slate-400 flex flex-col gap-0.5">
          Baseline (años)
          <input
            type="number" min={0} max={5} value={form.baseline_anos}
            onChange={(e) => setForm((f) => ({ ...f, baseline_anos: parseInt(e.target.value) ?? 3 }))}
            className="bg-black/25 border border-white/10 rounded-md px-2 py-1 text-sm text-slate-100 w-20"
          />
        </label>
        <button
          onClick={lanzar} disabled={!!job}
          className="rounded-lg bg-emerald-400 text-emerald-950 px-3 py-1.5 text-xs font-bold disabled:opacity-50 hover:brightness-110 transition"
        >
          {res ? 'Re-peritar' : 'Peritar'}
        </button>
      </div>

      {(form.tipo_evento === 'granizo' || form.tipo_evento === 'viento') && (
        <p className="text-[10px] text-amber-300/70 -mt-2 mb-3">
          ℹ️ Para tormentas nocturnas: declará el <b>día después</b> de la noche de tormenta
          (ej. si fue la noche del 18 al 19, poné 19). El escaneo satelital GOES
          (overshooting/rayos) busca la noche anterior a esta fecha; NDVI/ERA5 toleran
          mejor el día exacto.
        </p>
      )}

      {/* Datos del siniestro (opcional): póliza/aseguradora/productor/comentarios. */}
      <button
        onClick={() => setShowSiniestro((v) => !v)}
        className="text-[11px] text-slate-400 hover:text-slate-200 transition self-start mb-2 flex items-center gap-1"
      >
        {showSiniestro ? '▾' : '▸'} 🗂️ Datos del siniestro (opcional)
      </button>
      {showSiniestro && (
        <div className="flex flex-wrap gap-3 mb-3 shrink-0 bg-white/5 border border-white/10 rounded-lg p-3">
          <label className="text-[11px] text-slate-400 flex flex-col gap-0.5">
            Aseguradora
            <input
              type="text" value={form.aseguradora} placeholder="Ej: La Segunda Seguros"
              onChange={(e) => setForm((f) => ({ ...f, aseguradora: e.target.value }))}
              className="bg-black/25 border border-white/10 rounded-md px-2 py-1 text-sm text-slate-100 w-48"
            />
          </label>
          <label className="text-[11px] text-slate-400 flex flex-col gap-0.5">
            N° de póliza
            <input
              type="text" value={form.numero_poliza} placeholder="Ej: POL-2026-000123"
              onChange={(e) => setForm((f) => ({ ...f, numero_poliza: e.target.value }))}
              className="bg-black/25 border border-white/10 rounded-md px-2 py-1 text-sm text-slate-100 w-40"
            />
          </label>
          <label className="text-[11px] text-slate-400 flex flex-col gap-0.5">
            Productor / asegurado
            <input
              type="text" value={form.productor} placeholder="Ej: Juan Pérez"
              onChange={(e) => setForm((f) => ({ ...f, productor: e.target.value }))}
              className="bg-black/25 border border-white/10 rounded-md px-2 py-1 text-sm text-slate-100 w-48"
            />
          </label>
          <label className="text-[11px] text-slate-400 flex flex-col gap-0.5 flex-1 min-w-[200px]">
            Comentarios del perito
            <textarea
              value={form.comentarios_perito} rows={2} placeholder="Observaciones de campo, contexto adicional…"
              onChange={(e) => setForm((f) => ({ ...f, comentarios_perito: e.target.value }))}
              className="bg-black/25 border border-white/10 rounded-md px-2 py-1 text-sm text-slate-100 resize-none"
            />
          </label>
        </div>
      )}

      {err && <div className="text-sm text-rose-300 bg-rose-500/10 border border-rose-500/25 rounded-lg px-3 py-2 mb-3">{err}</div>}

      {job && (
        <div className="py-6">
          <LoadingMessages progreso={job.progreso} mensaje={job.mensaje}
            msgs={['Autenticando con Copernicus CDSE…', 'Bajando NDVI pre y post evento…', 'Calculando baseline histórico…', 'Clasificando daño por píxel…']} />
          <p className="text-[10px] text-slate-500 text-center mt-2">Puede tardar varios minutos (descarga varios composites satelitales).</p>
        </div>
      )}

      {res && !job && <Resultado res={res} />}

      {!res && !job && !err && (
        <p className="text-sm text-slate-400">
          Elegí la fecha y el tipo de evento y hacé clic en <b>Peritar</b>. Se comparará el NDVI antes/después
          contra un baseline histórico para estimar la superficie dañada.
        </p>
      )}

      <div className="text-[9px] text-slate-500 mt-auto pt-3 leading-relaxed border-t border-white/5">
        * Estimación satelital orientativa (Sentinel-2 L2A, Copernicus). No reemplaza la inspección a campo; genera puntos de muestreo y reporte para el perito.
      </div>
    </div>
  )
}

function Resultado({ res }) {
  const a = res.areas_ha || {}
  const p = res.pct || {}
  const out = res.outputs || {}
  const ct = res.confirmacion_termica
  const og = res.overshooting_goes
  const rg = res.rayos_goes
  const total = res.area_total_ha || 0
  const badge = COLOR_EVENTO[res.tipo_evento] || '#555'

  const warnings = res.warnings || []
  const noAtribuible = (res.interpretacion || '').includes('NO ATRIBUIBLE')
  const meta = res.metadata || {}
  const tieneMetadata = meta.aseguradora || meta.numero_poliza || meta.productor

  return (
    <div className="flex flex-col gap-3 animate-fadein">
      {/* Encabezado */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full text-white" style={{ background: badge }}>
          {res.tipo_evento}
        </span>
        <span className="text-sm text-slate-300">{res.nombre_caso} · {res.fecha_evento}</span>
        <ConfBadge conf={res.confianza} nEsc={res.n_escenas} />
        {noAtribuible && (
          <span className="text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full bg-amber-500/25 text-amber-200 border border-amber-400/40">
            No confirmado
          </span>
        )}
      </div>

      {/* Datos del siniestro (aseguradora/póliza/productor), si se cargaron */}
      {tieneMetadata && (
        <div className="text-[11px] text-slate-300 bg-white/5 border border-white/10 rounded-lg px-3 py-2 flex flex-wrap gap-x-4 gap-y-1">
          {meta.aseguradora && <span>🏢 <b>{meta.aseguradora}</b></span>}
          {meta.numero_poliza && <span>📄 Póliza <b>{meta.numero_poliza}</b></span>}
          {meta.productor && <span>👤 <b>{meta.productor}</b></span>}
        </div>
      )}

      {/* Warnings de sanidad del análisis (NDVI base anómalo, etc.) */}
      {warnings.length > 0 && (
        <div className="flex flex-col gap-1.5">
          {warnings.map((w, i) => (
            <div key={i} className="text-[11px] text-amber-200 bg-amber-500/10 border border-amber-500/30 rounded-lg px-3 py-2 leading-relaxed">
              {w}
            </div>
          ))}
        </div>
      )}

      {/* Métricas clave */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <Card label="Área total" val={`${total.toFixed(0)} ha`} />
        <Card label="Afectado" val={`${(a.afectada ?? 0).toFixed(0)} ha`} sub={`${(p.afectada ?? 0).toFixed(1)}%`} rojo />
        <Card label="NDVI pre→post" val={`${res.ndvi_pre} → ${res.ndvi_post}`} />
        <Card 
          label="ΔNDVI ajustado" 
          val={res.delta_adj} 
          sub="anomalía sobre baseline" 
          title={`Fórmula: ΔNDVI = (NDVI Post - NDVI Pre) - Baseline histórico\nCalculado como: (${res.ndvi_post} - ${res.ndvi_pre}) - (${res.baseline}) = ${res.delta_adj}`}
        />
      </div>

      {/* Barra de severidad */}
      <div>
        <div className="text-[11px] text-slate-400 mb-1">Distribución del daño</div>
        <div className="flex h-8 rounded-lg overflow-hidden text-[10px] font-bold">
          {['severa', 'moderada', 'leve', 'sin_dano'].map((k) => {
            const pct = p[k] ?? 0
            if (pct < 0.5) return null
            return (
              <div key={k} title={`${SEV[k].t}: ${(a[k] ?? 0).toFixed(0)} ha (${pct.toFixed(1)}%)`}
                className="grid place-items-center text-white/95 overflow-hidden whitespace-nowrap px-1"
                style={{ width: `${pct}%`, background: SEV[k].c }}>
                {pct >= 8 ? `${pct.toFixed(0)}%` : ''}
              </div>
            )
          })}
        </div>
        <div className="flex gap-3 mt-1.5 flex-wrap text-[10px] text-slate-400">
          {['severa', 'moderada', 'leve', 'sin_dano'].map((k) => (
            <span key={k} className="flex items-center gap-1">
              <span className="inline-block w-2.5 h-2.5 rounded-sm" style={{ background: SEV[k].c }} />
              {SEV[k].t}: {(a[k] ?? 0).toFixed(0)} ha
            </span>
          ))}
        </div>
      </div>

      {/* Interpretación */}
      {res.interpretacion && (
        <div className="text-[12px] text-slate-200 bg-white/5 border-l-4 rounded-r-lg px-3 py-2 leading-relaxed"
          style={{ borderColor: badge }}>
          {res.interpretacion}
        </div>
      )}

      {/* Verificación Física del Evento */}
      {ct && (
        <div className={`text-[11px] rounded-lg px-3 py-2 border leading-relaxed ${
          ct.disponible
            ? (ct.confirmada ? 'bg-sky-500/10 border-sky-500/30 text-sky-200' : 'bg-slate-500/10 border-slate-500/25 text-slate-300')
            : 'bg-amber-500/10 border-amber-500/30 text-amber-300'}`}>
          <b>📊 Verificación Física (Open-Meteo Archive): </b>
          {ct.disponible
            ? <>{ct.confirmada ? '✅' : '⚪'} {ct.clasificacion} {ct.detalles ? `· ${ct.detalles}` : (ct.t_min_c != null ? `· mínima de ${ct.t_min_c}°C el ${ct.noche_mas_fria}` : '')}</>
            : <>⚠️ no verificable{ct.error ? ` (${ct.error})` : ''}.</>}
        </div>
      )}

      {/* Overshooting tops GOES-19 — tercera fuente independiente (solo granizo/viento) */}
      {og && (
        <div className={`text-[11px] rounded-lg px-3 py-2 border leading-relaxed ${
          !og.disponible
            ? 'bg-amber-500/10 border-amber-500/30 text-amber-300'
            : og.overshooting
              ? 'bg-rose-500/10 border-rose-500/30 text-rose-200'
              : 'bg-slate-500/10 border-slate-500/25 text-slate-300'}`}>
          <b>🛰️ Overshooting Tops (GOES-19 ABI): </b>
          {og.disponible
            ? (og.granules_con_dato > 0
                ? <>{og.overshooting ? '🧊' : '⚪'} {og.overshooting ? 'Overshooting top detectado' : `sin firma de overshooting (nivel: ${og.nivel})`}
                  {og.ctt_min_c != null && <> · tope {og.ctt_min_c}°C ({og.delta_k}K bajo el yunque) a las {og.hora_pico?.slice(11, 16)} UTC</>}
                  {' '}· {og.granules_leidos} imágenes escaneadas</>
                : <>⚪ cielo despejado en toda la ventana escaneada.</>)
            : <>⚠️ no verificable{og.error ? ` (${og.error})` : ''}.</>}
        </div>
      )}

      {/* Rayos GOES-19 GLM — cuarta fuente independiente (solo granizo/viento) */}
      {rg && (
        <div className={`text-[11px] rounded-lg px-3 py-2 border leading-relaxed ${
          !rg.disponible
            ? 'bg-amber-500/10 border-amber-500/30 text-amber-300'
            : rg.nivel === 'alta'
              ? 'bg-rose-500/10 border-rose-500/30 text-rose-200'
              : rg.nivel === 'media'
                ? 'bg-amber-500/10 border-amber-500/25 text-amber-200'
                : 'bg-slate-500/10 border-slate-500/25 text-slate-300'}`}>
          <b>⚡ Rayos (GOES-19 GLM): </b>
          {rg.disponible
            ? <>{rg.nivel === 'alta' || rg.nivel === 'media' ? '⚡' : '⚪'} Nivel <b>{rg.nivel}</b>
              {' '}· <b>~{rg.descargas_totales?.toLocaleString('es-AR')}</b> descargas totales (estimado)
              {rg.descargas_pico_hora > 0 && <> · pico ~{rg.descargas_pico_hora}/h a las {rg.hora_pico?.slice(11, 16)} UTC</>}
              {' '}· {rg.granules_leidos} archivos GLM escaneados (submuestreo)</>
            : <>⚠️ no verificable{rg.error ? ` (${rg.error})` : ''}.</>}
        </div>
      )}

      {/* Comentarios del perito, si se cargaron */}
      {meta.comentarios_perito && (
        <div className="text-[12px] text-slate-300 bg-white/5 border border-white/10 rounded-lg px-3 py-2 leading-relaxed whitespace-pre-line">
          <b>📝 Comentarios del perito:</b> {meta.comentarios_perito}
        </div>
      )}

      {/* Enlaces a salidas */}
      <div className="flex gap-2 flex-wrap">
        {out.reporte_html && <LinkBtn href={out.reporte_html}>📄 Reporte HTML</LinkBtn>}
        {out.reporte_pdf && <LinkBtn href={out.reporte_pdf}>📕 Reporte PDF (expediente)</LinkBtn>}
        {out.comparativa_png && <LinkBtn href={out.comparativa_png}>🖼️ Comparativa NDVI</LinkBtn>}
        {out.visor_html && <LinkBtn href={out.visor_html}>🗺️ Visor de campo (GPS)</LinkBtn>}
        {out.peritaje_csv && <LinkBtn href={out.peritaje_csv}>📍 Puntos (CSV)</LinkBtn>}
        {out.peritaje_kml && <LinkBtn href={out.peritaje_kml}>📌 Puntos (KML)</LinkBtn>}
        {out.metricas_csv && <LinkBtn href={out.metricas_csv}>📊 Métricas (CSV)</LinkBtn>}
      </div>
    </div>
  )
}

const Vacio = ({ children }) => <div className="h-full grid place-items-center text-slate-400 text-sm">{children}</div>

const Card = ({ label, val, sub, rojo, title }) => (
  <div className={`bg-white/5 border border-white/10 rounded-xl px-3 py-2 ${title ? 'cursor-help' : ''}`} title={title}>
    <div className="text-[10px] text-slate-400">{label}</div>
    <div className={`text-lg font-bold ${rojo ? 'text-rose-300' : 'text-slate-100'}`}>{val}</div>
    {sub && <div className="text-[10px] text-slate-500">{sub}</div>}
  </div>
)

const ConfBadge = ({ conf, nEsc }) => {
  const clr = { ALTA: 'bg-emerald-400/20 text-emerald-300', MEDIA: 'bg-amber-400/20 text-amber-300', BAJA: 'bg-rose-400/20 text-rose-300' }[conf] || 'bg-slate-400/20 text-slate-300'
  return (
    <span className={`text-[10px] rounded-full px-2 py-0.5 ${clr}`}>
      Confianza {conf}{nEsc ? ` · ${nEsc.pre}/${nEsc.post} esc` : ''}
    </span>
  )
}

const LinkBtn = ({ href, children }) => (
  <a href={href} target="_blank" rel="noreferrer"
    className="text-[11px] text-sky-300 bg-sky-400/10 border border-sky-400/25 rounded-lg px-2.5 py-1.5 hover:bg-sky-400/20 transition">
    {children}
  </a>
)
