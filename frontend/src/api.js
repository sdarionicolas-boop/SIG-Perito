// Cliente HTTP fino sobre la API FastAPI (mismo origen en producción).
async function handle(r) {
  if (!r.ok) {
    const body = await r.json().catch(() => ({}))
    throw new Error(body.detail || r.statusText)
  }
  return r.json()
}

// Helper para abortar peticiones colgadas al clima externo en el navegador del cliente (evita bloqueos infinitos)
async function fetchWithTimeout(resource, options = {}) {
  const { timeout = 3000 } = options;
  const controller = new AbortController();
  const id = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(resource, {
      ...options,
      signal: controller.signal
    });
    clearTimeout(id);
    return response;
  } catch (e) {
    clearTimeout(id);
    throw e;
  }
}

// Caché en memoria con TTL: si el usuario vuelve a un lote que ya vio hace poco
// (ida y vuelta entre lotes), evita re-pedirle a Open-Meteo lo mismo. Solo cachea
// respuestas EXITOSAS -- una falla no queda "pegada", el próximo intento reintenta.
const CACHE_TTL_MS = 10 * 60 * 1000 // 10 minutos: un pronóstico no cambia tan rápido
const _cache = new Map()
async function conCache(key, fn) {
  const hit = _cache.get(key)
  if (hit && Date.now() - hit.ts < CACHE_TTL_MS) return hit.data
  const data = await fn()
  _cache.set(key, { data, ts: Date.now() })
  return data
}

const getJSON = (path) => fetch(path).then(handle)
const postJSON = (path, body) =>
  fetch(path, {
    method: 'POST',
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  }).then(handle)

export const api = {
  lotes: () => getJSON('/api/lotes'),
  mapa: () => getJSON('/api/lotes/mapa'),
  serie: (id, indice = 'NDVI') => getJSON(`/api/lotes/${id}/serie-temporal?indice=${indice}`),
  forecast: (id, coords) => conCache(`forecast:${id}`, async () => {
    if (coords && coords.lat != null && coords.lon != null) {
      const { lat, lon } = coords
      const url = `https://api.open-meteo.com/v1/gfs?latitude=${lat}&longitude=${lon}&hourly=temperature_2m,relative_humidity_2m&daily=temperature_2m_max,temperature_2m_min&forecast_days=16&timezone=America/Argentina/Cordoba`
      try {
        const raw = await fetchWithTimeout(url, { timeout: 3500 }).then(async (r) => {
          if (!r.ok) {
            const txt = await r.text().catch(() => '')
            throw new Error(`Open-Meteo HTTP ${r.status}: ${txt}`)
          }
          return r.json()
        })
        return postJSON(`/api/lotes/${id}/forecast`, raw)
      } catch (e) {
        // La consulta directa (IP del usuario) evita el 429 del servidor, pero si
        // el navegador no puede alcanzar Open-Meteo (red/extensión/CORS/firewall),
        // caemos al GET server-side: el backend la consulta por su cuenta.
        console.warn('Open-Meteo directo falló, uso GET server-side:', e.message)
      }
    }
    return getJSON(`/api/lotes/${id}/forecast`)
  }),
  rinde: (id) => getJSON(`/api/lotes/${id}/rinde`),
  rindePotencial: (id, cultivo) =>
    getJSON(`/api/lotes/${id}/rinde-potencial${cultivo ? `?cultivo=${encodeURIComponent(cultivo)}` : ''}`),
  zonif: (id) => getJSON(`/api/lotes/${id}/zonificacion`),
  lanzarZonif: (id) => postJSON(`/api/lotes/${id}/zonificacion`),
  // Peritaje de eventualidades: lanza un job (POST) -> sondear con pollJob(job_id).
  lanzarPeritaje: (id, body) => postJSON(`/api/lotes/${id}/peritaje`, body),
  peritaje: (id) => getJSON(`/api/lotes/${id}/peritaje`),
  siniestros: (filtros = {}) => {
    const qs = new URLSearchParams(Object.entries(filtros).filter(([, v]) => v))
    const s = qs.toString()
    return getJSON(`/api/siniestros${s ? `?${s}` : ''}`)
  },
  margen: (id, body) => postJSON(`/api/lotes/${id}/margen-bruto`, body),
  job: (id) => getJSON(`/api/jobs/${id}`),
  altaGeojson: (body) => postJSON('/api/lotes/geojson', body),
  sanidad: async (id, { cultivo, zona, etapa, coords } = {}) => {
    const qs = new URLSearchParams()
    if (cultivo) qs.set('cultivo', cultivo)
    if (zona) qs.set('zona', zona)
    if (etapa) qs.set('etapa', etapa)
    const s = qs.toString() ? '?' + qs.toString() : ''
    // Bypass 429: si tenemos coords, consultamos Open-Meteo desde el navegador
    // (IP del usuario) y POSTeamos el JSON crudo; el backend solo puntúa.
    if (coords && coords.lat != null && coords.lon != null) {
      const url = `https://api.open-meteo.com/v1/forecast?latitude=${coords.lat}&longitude=${coords.lon}` +
        `&hourly=temperature_2m,precipitation,soil_moisture_0_to_10cm&past_days=15&forecast_days=0` +
        `&timezone=America/Argentina/Cordoba`
      try {
        const raw = await fetchWithTimeout(url, { timeout: 3500 }).then((r) => {
          if (!r.ok) throw new Error(`Open-Meteo HTTP ${r.status}`)
          return r.json()
        })
        return postJSON(`/api/lotes/${id}/sanidad${s}`, raw)
      } catch (e) {
        // Si la consulta directa falla, caemos al GET (clima server-side).
        console.warn('Open-Meteo directo falló, uso GET server-side:', e.message)
      }
    }
    return getJSON(`/api/lotes/${id}/sanidad${s}`)
  },

  cosecha: (id) => getJSON(`/api/lotes/${id}/cosecha`),
  validacion: (id) => getJSON(`/api/lotes/${id}/validacion`),
  validacionGlobal: () => getJSON('/api/validacion'),
  metricas: () => getJSON('/api/admin/metricas'),
  usoSuelo: (id) => getJSON(`/api/uso-suelo/${id}`),
  compliance: (id) => getJSON(`/api/compliance/${id}`),
  alertasClima: (id) => getJSON(`/api/lotes/${id}/alertas-clima`),
  precipitacionWrf: (id, horas = 12) => getJSON(`/api/lotes/${id}/precipitacion-wrf?horas=${horas}`),
  rayosGoes: (id, { radioKm = 40, ventanaMin = 10 } = {}) =>
    getJSON(`/api/lotes/${id}/rayos-goes?radio_km=${radioKm}&ventana_min=${ventanaMin}`),
  overshootingGoes: (id, { radioKm = 30 } = {}) =>
    getJSON(`/api/lotes/${id}/overshooting-goes?radio_km=${radioKm}`),
  avisosSmn: (id) => getJSON(`/api/lotes/${id}/avisos-smn`),
  desvioNdvi: (id) => getJSON(`/api/lotes/${id}/desvio-ndvi`),
  carbono: (id) => getJSON(`/api/carbono/${id}`),
}


// Sondea un job hasta COMPLETED/FAILED. onTick(estado) para feedback.
export async function pollJob(jobId, onTick, intervalMs = 2500, maxTries = 60) {
  for (let i = 0; i < maxTries; i++) {
    const j = await api.job(jobId)
    onTick?.(j)
    if (j.estado === 'COMPLETED' || j.estado === 'FAILED') return j
    await new Promise((r) => setTimeout(r, intervalMs))
  }
  throw new Error('Timeout esperando el job.')
}

export const COLORS = ['#34d399', '#60a5fa', '#fbbf24', '#f87171', '#a78bfa']
export const ZONA_COLOR = { Bajo: '#ef4444', Medio: '#fbbf24', Alto: '#34d399' }
