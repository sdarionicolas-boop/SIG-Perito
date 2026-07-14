import { useEffect, useState } from 'react'
import { api } from '../api'
import LoadingMessages from './LoadingMessages'

const SECADO_CLR = {
  Excelente: 'bg-emerald-400/15 text-emerald-300',
  Moderado: 'bg-amber-400/15 text-amber-300',
  Nulo: 'bg-slate-400/15 text-slate-300',
}

// Color del badge de probabilidad según la gravedad del evento.
const GRAV_CLR = {
  alta: 'bg-rose-500/25 text-rose-200 border-rose-400/40',
  media: 'bg-amber-500/20 text-amber-200 border-amber-400/40',
  baja: 'bg-slate-500/20 text-slate-200 border-slate-400/40',
}

export default function ForecastPanel({ active, lote }) {
  const [fc, setFc] = useState(null)
  const [alertas, setAlertas] = useState(null)
  const [err, setErr] = useState(null)
  const [wrf, setWrf] = useState(null)
  const [wrfLoading, setWrfLoading] = useState(false)
  const [glm, setGlm] = useState(null)
  const [glmLoading, setGlmLoading] = useState(false)
  const [ot, setOt] = useState(null)
  const [otLoading, setOtLoading] = useState(false)
  const [smn, setSmn] = useState(null)
  const [smnLoading, setSmnLoading] = useState(false)

  useEffect(() => {
    setFc(null); setErr(null); setAlertas(null)
    setWrf(null); setWrfLoading(false); setGlm(null); setGlmLoading(false)
    setOt(null); setOtLoading(false); setSmn(null); setSmnLoading(false)
    if (active == null) return
    let cancel = false
    const coords = lote ? { lat: lote.centroide_lat, lon: lote.centroide_lon } : null

    api.forecast(active, coords)
      .then((d) => !cancel && setFc(d))
      .catch((e) => !cancel && setErr(e.message))

    api.alertasClima(active)
      .then((d) => !cancel && setAlertas(d))
      .catch(() => {})

    return () => { cancel = true }
  }, [active, lote])

  // Precipitación WRF-DET 4 km del SMN: carga bajo demanda (archivos ~17 MB).
  const loadWrf = () => {
    setWrfLoading(true)
    api.precipitacionWrf(active)
      .then(setWrf)
      .catch((e) => setWrf({ disponible: false, error: e.message }))
      .finally(() => setWrfLoading(false))
  }

  // Actividad eléctrica en tiempo real (GOES-19 GLM): bajo demanda (~45 MB).
  const loadGlm = () => {
    setGlmLoading(true)
    api.rayosGoes(active)
      .then(setGlm)
      .catch((e) => setGlm({ disponible: false, error: e.message }))
      .finally(() => setGlmLoading(false))
  }

  // Topes nubosos penetrantes (GOES-19 ABI): bajo demanda (disco completo, ~decenas MB).
  const loadOt = () => {
    setOtLoading(true)
    api.overshootingGoes(active)
      .then(setOt)
      .catch((e) => setOt({ disponible: false, error: e.message }))
      .finally(() => setOtLoading(false))
  }

  // Avisos oficiales del SMN (vía Alert Hub CAP de la WMO): bajo demanda.
  const loadSmn = () => {
    setSmnLoading(true)
    api.avisosSmn(active)
      .then(setSmn)
      .catch((e) => setSmn({ disponible: false, error: e.message }))
      .finally(() => setSmnLoading(false))
  }

  if (active == null) return <Vacio>Seleccioná un lote para ver el pronóstico.</Vacio>
  if (err) return <Vacio>Error: {err}</Vacio>
  if (!fc) return <div className="h-full grid place-items-center"><LoadingMessages msgs={['Consultando NOAA GFS…']} /></div>

  const r = fc.resumen || {}
  return (
    <div className="h-full flex flex-col p-4 overflow-y-auto animate-fadein">
      <div className="text-xs uppercase tracking-wide text-slate-400 font-semibold mb-2">
        Pronóstico 16 días · {fc.modelo}
      </div>

      {/* Grid de dos columnas para alertas y telemetría */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 mb-3 shrink-0">
        
        {/* Columna Izquierda: Advertencias de Riesgo y Alertas Oficiales */}
        <div className="flex flex-col gap-3">
          {/* Franco's Convective Warnings Block */}
          {alertas && alertas.error && (
            <div className="bg-amber-500/10 border border-amber-500/35 text-amber-300 text-[11px] rounded-xl p-3 flex flex-col gap-1 leading-relaxed">
              <div className="font-bold flex items-center gap-1.5">
                <span>⚠️</span>
                <span>ADVERTENCIA DE RIESGO CLIMÁTICO NO VERIFICADO</span>
              </div>
              <div>
                No se pudieron comprobar las condiciones de tormenta severa locales (error de conexión o límite de cuota). Por favor, verifique el estado del tiempo mediante alertas meteorológicas oficiales.
              </div>
            </div>
          )}

          {alertas && alertas.hay_alertas && !alertas.error && (
            <div className={`bg-rose-500/10 border border-rose-500/30 text-rose-300 text-xs rounded-xl p-3 flex flex-col gap-1.5 ${alertas.alertas_lista.some(al => al.gravedad === 'alta') ? 'animate-pulse' : ''}`}>
              <div className="font-bold flex items-center gap-1.5 text-[11px]">
                <span>⛈️</span>
                <span>ADVERTENCIA DE RIESGO CLIMÁTICO PROYECTADO</span>
                {alertas.fuente === 'simulado' && (
                  <span className="text-[9px] bg-rose-500/20 text-rose-300 border border-rose-500/30 px-1 py-0.5 rounded uppercase font-bold">
                    Demo
                  </span>
                )}
              </div>
              <div className="flex flex-col gap-1 text-[11px] text-slate-300">
                {alertas.alertas_lista.map((al, idx) => (
                  <div key={idx} className="border-l-2 border-rose-500 pl-2 py-0.5">
                    {al.prob_pct != null && (
                      <span className={`inline-block text-[10px] font-bold border rounded px-1 mr-1 align-middle ${GRAV_CLR[al.gravedad] || GRAV_CLR.baja}`}>
                        {al.prob_pct}%
                      </span>
                    )}
                    <span className="font-bold text-rose-300">{al.tipo}: </span>
                    <span>{al.mensaje} </span>
                    <span className="text-slate-400 font-semibold">({al.fecha})</span>
                  </div>
                ))}
              </div>
              <div className="text-[9px] text-rose-400/50 border-t border-rose-500/20 pt-1.5 mt-1 leading-relaxed">
                * Información de carácter consultivo sobre riesgo climático regional (NOAA GFS). La ausencia de aviso no garantiza la ausencia de fenómenos severos.
              </div>
            </div>
          )}

          {alertas && !alertas.hay_alertas && !alertas.error && (
            <div className="bg-emerald-500/10 border border-emerald-500/25 text-emerald-300 text-[11px] rounded-xl px-3 py-2 leading-relaxed">
              ✓ No se identificaron condiciones extremas que superen los umbrales de riesgo del sistema para las próximas 72 hs. La ausencia de alerta no garantiza la ausencia de eventos climáticos severos.
            </div>
          )}

          {/* Aviso OFICIAL del SMN — Alert Hub CAP de la WMO (mayor peso legal) */}
          {alertas && !alertas.error && (
            <div className="w-full">
              {!smn && !smnLoading && (
                <button
                  onClick={loadSmn}
                  className="w-full text-left text-[11px] text-rose-200 bg-rose-500/10 border border-rose-400/30 rounded-lg px-3 py-1.5 hover:bg-rose-500/20 transition"
                >
                  🏛️ Consultar avisos oficiales del SMN
                </button>
              )}
              {smnLoading && (
                <div className="text-[11px] text-rose-200/80">
                  <LoadingMessages msgs={['Consultando avisos oficiales del SMN (WMO)…', 'Cruzando polígonos con el lote…']} />
                </div>
              )}
              {smn && smn.disponible && smn.vigente && (
                <div className="bg-rose-600/15 border border-rose-500/30 rounded-xl p-3 text-[11px] text-rose-100 flex flex-col gap-1">
                  <div className="font-bold flex items-center gap-1.5">
                    <span>🏛️</span><span>AVISO OFICIAL DEL SMN VIGENTE</span>
                  </div>
                  {smn.avisos.map((a, i) => (
                    <div key={i} className="border-l-2 border-rose-400 pl-2">
                      <b>{a.event}</b>{a.severity ? ` · ${a.severity}` : ''}
                      {a.expires ? <span className="text-rose-200/70"> · hasta {a.expires.slice(0, 16).replace('T', ' ')}</span> : null}
                    </div>
                  ))}
                  <div className="text-[9px] text-rose-200/60 border-t border-rose-500/30 pt-1 mt-0.5">
                    Emitido por el {smn.avisos[0]?.fuente_oficial || 'Servicio Meteorológico Nacional'} · vía Alert Hub (WMO).
                  </div>
                </div>
              )}
              {smn && smn.disponible && !smn.vigente && (
                <div className="text-[11px] text-slate-400 bg-white/5 border border-white/10 rounded-lg px-3 py-1.5">
                  🏛️ Sin avisos oficiales del SMN vigentes para este lote ({smn.total_pais} activos).
                </div>
              )}
              {smn && !smn.disponible && (
                <div className="text-[10px] text-amber-300/70">
                  No se pudieron consultar los avisos del SMN{smn.error ? ` (${smn.error})` : ''}.
                </div>
              )}
            </div>
          )}
        </div>

        {/* Columna Derecha: Telemetría y Consulta de Modelos Lluvia/Rayos */}
        <div className="flex flex-col gap-3">
          {/* Precipitación de alta resolución — WRF-DET 4 km del SMN (bajo demanda) */}
          {alertas && !alertas.error && (
            <div className="w-full">
              {!wrf && !wrfLoading && (
                <button
                  onClick={loadWrf}
                  className="w-full text-left text-[11px] text-sky-300 bg-sky-400/10 border border-sky-400/25 rounded-lg px-3 py-1.5 hover:bg-sky-400/20 transition"
                >
                  💧 Ver precipitación de alta resolución (SMN 4 km)
                </button>
              )}
              {wrfLoading && (
                <div className="text-[11px] text-sky-300/80">
                  <LoadingMessages msgs={['Descargando WRF-DET 4 km del SMN…', 'Muestreando la celda del lote…']} />
                </div>
              )}
              {wrf && wrf.disponible && (
                <div className="bg-sky-500/5 border border-sky-500/20 rounded-xl p-3 text-[11px] text-slate-300">
                  <div className="font-semibold text-sky-300 mb-1.5">
                    💧 Precipitación SMN WRF-DET · 4 km · pico <b>{wrf.pico_mm} mm/h</b>
                  </div>
                  <div className="flex gap-0.5 items-end h-8">
                    {wrf.serie.map((h, i) => (
                      <div
                        key={i}
                        title={`${h.hora.slice(11, 16)} · ${h.pp_mm} mm`}
                        className="flex-1 bg-sky-400/40 rounded-sm min-h-[2px]"
                        style={{ height: `${Math.min(100, (h.pp_mm / Math.max(wrf.pico_mm, 1)) * 100)}%` }}
                      />
                    ))}
                  </div>
                  <div className="text-[9px] text-slate-500 mt-1.5 leading-relaxed">
                    Fuente oficial SMN (AWS Open Data){wrf.ciclo ? ` · ciclo ${wrf.ciclo.slice(0, 16).replace('T', ' ')} UTC` : ''}.
                  </div>
                </div>
              )}
              {wrf && !wrf.disponible && (
                <div className="text-[10px] text-amber-300/70">
                  No se pudo obtener el WRF-DET del SMN{wrf.error ? ` (${wrf.error})` : ''}.
                </div>
              )}
            </div>
          )}

          {/* Actividad eléctrica en tiempo real — GOES-19 GLM (bajo demanda) */}
          {alertas && !alertas.error && (
            <div className="w-full">
              {!glm && !glmLoading && (
                <button
                  onClick={loadGlm}
                  className="w-full text-left text-[11px] text-violet-300 bg-violet-400/10 border border-violet-400/25 rounded-lg px-3 py-1.5 hover:bg-violet-400/20 transition"
                >
                  ⚡ Ver actividad eléctrica en tiempo real (GOES-19)
                </button>
              )}
              {glmLoading && (
                <div className="text-[11px] text-violet-300/80">
                  <LoadingMessages msgs={['Barriendo descargas GLM de GOES-19…', 'Filtrando alrededor del lote…']} />
                </div>
              )}
              {glm && glm.disponible && (
                <div className={`rounded-xl p-2.5 text-[11px] border ${glm.activo ? 'bg-violet-500/10 border-violet-500/30 text-violet-200' : 'bg-emerald-500/5 border-emerald-500/20 text-emerald-300'}`}>
                  <div className="font-semibold flex items-center gap-1.5">
                    <span>⚡</span>
                    {glm.activo ? (
                      <span>Actividad eléctrica: <b>{glm.descargas}</b> descargas en {glm.radio_km} km</span>
                    ) : (
                      <span>Sin descargas en {glm.radio_km} km ({glm.ventana_min} min)</span>
                    )}
                  </div>
                  <div className="text-[9px] text-slate-500 mt-1 leading-relaxed">
                    GOES-19 GLM (NOAA, tiempo real). Proxy de convección.
                  </div>
                </div>
              )}
              {glm && !glm.disponible && (
                <div className="text-[10px] text-amber-300/70">
                  No se pudo obtener GLM de GOES-19{glm.error ? ` (${glm.error})` : ''}.
                </div>
              )}
            </div>
          )}

          {/* Topes nubosos penetrantes — GOES-19 ABI Cloud Top Temperature (bajo demanda) */}
          {alertas && !alertas.error && (
            <div className="w-full">
              {!ot && !otLoading && (
                <button
                  onClick={loadOt}
                  className="w-full text-left text-[11px] text-cyan-300 bg-cyan-400/10 border border-cyan-400/25 rounded-lg px-3 py-1.5 hover:bg-cyan-400/20 transition"
                >
                  🧊 Ver topes nubosos penetrantes (GOES-19 ABI)
                </button>
              )}
              {otLoading && (
                <div className="text-[11px] text-cyan-300/80">
                  <LoadingMessages msgs={['Descargando disco completo GOES-19 ABI…', 'Buscando cúpulas sobre la tropopausa…']} />
                </div>
              )}
              {ot && ot.disponible && (
                <div className={`rounded-xl p-2.5 text-[11px] border ${ot.overshooting ? 'bg-rose-500/10 border-rose-500/30 text-rose-200 animate-pulse' : ot.activo ? 'bg-cyan-500/10 border-cyan-500/30 text-cyan-200' : 'bg-emerald-500/5 border-emerald-500/20 text-emerald-300'}`}>
                  <div className="font-semibold flex items-center gap-1.5">
                    <span>🧊</span>
                    {ot.overshooting ? (
                      <span>Tope penetrante detectado · tope <b>{ot.ctt_min_c}°C</b> ({ot.delta_k}K bajo el yunque)</span>
                    ) : ot.activo ? (
                      <span>Convección profunda · tope más frío <b>{ot.ctt_min_c}°C</b></span>
                    ) : (
                      <span>Sin convección profunda en {ot.radio_km} km</span>
                    )}
                  </div>
                  <div className="text-[9px] text-slate-500 mt-1 leading-relaxed">
                    GOES-19 ABI (Cloud Top Temperature). Un tope bajo {ot.umbral_tropopausa_c}°C que sobresale del yunque indica granizo potencial.
                  </div>
                </div>
              )}
              {ot && !ot.disponible && (
                <div className="text-[10px] text-amber-300/70">
                  No se pudo obtener ABI de GOES-19{ot.error ? ` (${ot.error})` : ''}.
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      <div className="flex gap-2 flex-wrap mb-3">
        <Badge cls="bg-sky-400/15 text-sky-300">
          ❄️ Heladas (&lt;2°C): <b>{r.dias_con_helada ?? 0}</b>
          {r.proxima_helada ? ` · próxima ${r.proxima_helada}` : ''}
        </Badge>
        <Badge cls="bg-rose-400/15 text-rose-300">
          🌡️ Estrés térmico (&gt;35°C): <b>{r.dias_con_estres_termico ?? 0}</b>
        </Badge>
      </div>
      <div className="flex gap-2 overflow-x-auto pb-1 shrink-0">
        {(fc.dias || []).map((d) => (
          <div
            key={d.fecha}
            title={`VPD máx ${d.vpd_max ?? '—'} kPa`}
            className={[
              'shrink-0 w-[92px] rounded-xl p-2.5 text-center border border-white/10 bg-white/5',
              d.estres_confianza === 'tendencia' ? 'opacity-60 border-dashed' : '',
            ].join(' ')}
          >
            <div className="text-[11px] text-slate-400">{d.fecha.slice(5)}</div>
            <div className="text-sm font-bold my-1">
              <span className="text-sky-300">{d.t_min ?? '—'}°</span>
              <span className="text-slate-500"> / </span>
              <span className="text-rose-300">{d.t_max ?? '—'}°</span>
            </div>
            <div className="flex flex-col gap-1">
              {d.helada_agrometeorologica && <Mini cls="bg-sky-400/20 text-sky-300">❄️ helada</Mini>}
              {d.estres_termico && <Mini cls="bg-rose-400/20 text-rose-300">🌡️ {d.horas_estres_termico}h</Mini>}
              <Mini cls={SECADO_CLR[d.secado] || 'bg-slate-400/15 text-slate-300'}>
                secado {d.secado || '—'}
              </Mini>
            </div>
          </div>
        ))}
      </div>
      <div className="text-[9px] text-slate-500 mt-3 leading-relaxed border-t border-white/5 pt-2">
        * Los datos del pronóstico provienen de modelos globales predictivos (NOAA GFS) y son procesados automáticamente. Tienen carácter consultivo y orientativo para soporte de decisión agronómica. No constituyen alertas meteorológicas oficiales ni garantías de ocurrencia o ausencia de siniestros.
      </div>
    </div>
  )
}

const Vacio = ({ children }) => <div className="h-full grid place-items-center text-slate-400 text-sm">{children}</div>
const Badge = ({ cls, children }) => <div className={`text-xs px-3 py-1.5 rounded-lg ${cls}`}>{children}</div>
const Mini = ({ cls, children }) => <div className={`text-[10px] leading-tight rounded px-1 py-0.5 ${cls}`}>{children}</div>
