import { useEffect, useState, useCallback } from 'react'
import { api } from './api'
import Header from './components/Header'
import LoteList from './components/LoteList'
import MapView from './components/MapView'
import PeritajePanel from './components/PeritajePanel'
import ValidacionPanel from './components/ValidacionPanel'
import HeladaAlertPanel from './components/HeladaAlertPanel'
import SiniestrosPanel from './components/SiniestrosPanel'

const MAX_SEL = 5

export default function App() {
  const [lotes, setLotes] = useState([])
  const [fc, setFc] = useState(null)
  const [selected, setSelected] = useState([])      // ids para comparar (máx 5)
  const [active, setActive] = useState(null)        // lote activo (detalle)
  const [drawing, setDrawing] = useState(false)
  const [toast, setToast] = useState(null)
  const [zonifVersion, setZonifVersion] = useState(0)   // bump al recalcular zonificación
  const [peritajeVersion, setPeritajeVersion] = useState(0) // bump al completar un peritaje
  const [subTab, setSubTab] = useState('peritaje')

  const cargar = useCallback(async () => {
    const [ls, mapa] = await Promise.all([api.lotes(), api.mapa()])
    setLotes(ls)
    setFc(mapa)
    return ls
  }, [])

  useEffect(() => { cargar().catch((e) => setToast('Error: ' + e.message)) }, [cargar])

  const toggleSelect = (id) => {
    setSelected((prev) => {
      if (prev.includes(id)) return prev.filter((x) => x !== id)
      if (prev.length >= MAX_SEL) return prev
      return [...prev, id]
    })
    setActive(id)
  }

  const onActivar = (id) => {
    setActive(id)
    setSelected((prev) => (prev.includes(id) ? prev : [...prev.slice(-(MAX_SEL - 1)), id]))
  }

  // Desde el listado de casos: seleccionar el lote y saltar directo al detalle.
  const verCaso = (loteId) => {
    onActivar(loteId)
    setSubTab('peritaje')
  }

  const guardarLoteDibujado = async (coords) => {
    try {
      const geojson = { type: 'Polygon', coordinates: [coords] }
      const res = await api.altaGeojson({ nombre: 'Lote dibujado', geojson })
      setToast(`Lote #${res.id} guardado (${res.area_ha ?? '—'} ha).`)
      setDrawing(false)
      const ls = await cargar()
      onActivar(res.id || ls[ls.length - 1]?.id)
    } catch (e) {
      setToast('No se pudo guardar: ' + e.message)
    }
  }

  const supTotal = lotes.reduce((s, l) => s + (l.area_ha || 0), 0)
  const activoObj = lotes.find((l) => l.id === active) || null

  return (
    <div className="h-screen flex flex-col overflow-hidden">
      <Header
        nLotes={lotes.length}
        supTotal={supTotal}
        activo={activoObj}
      />

      <main className="flex flex-1 gap-3 p-3 min-h-0">
        <aside className="w-72 shrink-0">
          <LoteList
            lotes={lotes}
            selected={selected}
            active={active}
            onToggle={toggleSelect}
            drawing={drawing}
            setDrawing={setDrawing}
          />
        </aside>

        <section className="flex-1 flex flex-col gap-3 min-w-0">
          <div className="glass rounded-2xl overflow-hidden h-[46vh] min-h-[320px]">
            <MapView
              fc={fc}
              active={active}
              selected={selected}
              onActivar={onActivar}
              drawing={drawing}
              onSaveDraw={guardarLoteDibujado}
              zonifVersion={zonifVersion}
              peritajeVersion={peritajeVersion}
            />
          </div>

          <HeladaAlertPanel active={active} lote={activoObj} />

          <div className="glass rounded-2xl flex-1 min-h-[280px] animate-fadein flex flex-col overflow-hidden">
            <div className="flex border-b border-white/10 px-4 bg-white/5 shrink-0">
              <button
                onClick={() => setSubTab('peritaje')}
                className={`px-4 py-2.5 text-xs font-bold transition border-b-2 -mb-[1px] ${
                  subTab === 'peritaje'
                    ? 'border-emerald-400 text-emerald-400'
                    : 'border-transparent text-slate-400 hover:text-slate-200'
                }`}
              >
                🧊 Peritaje de Eventualidades
              </button>
              <button
                onClick={() => setSubTab('validacion')}
                className={`px-4 py-2.5 text-xs font-bold transition border-b-2 -mb-[1px] ${
                  subTab === 'validacion'
                    ? 'border-emerald-400 text-emerald-400'
                    : 'border-transparent text-slate-400 hover:text-slate-200'
                }`}
              >
                🔬 Consistencia de Datos (Calidad)
              </button>
              <button
                onClick={() => setSubTab('casos')}
                className={`px-4 py-2.5 text-xs font-bold transition border-b-2 -mb-[1px] ${
                  subTab === 'casos'
                    ? 'border-emerald-400 text-emerald-400'
                    : 'border-transparent text-slate-400 hover:text-slate-200'
                }`}
              >
                📁 Casos
              </button>
            </div>
            <div className="flex-1 overflow-y-auto min-h-0">
              {subTab === 'peritaje' && (
                <PeritajePanel active={active} lote={activoObj}
                  onPeritajeDone={() => setPeritajeVersion((v) => v + 1)} />
              )}
              {subTab === 'validacion' && <ValidacionPanel active={active} />}
              {subTab === 'casos' && <SiniestrosPanel onVerCaso={verCaso} />}
            </div>
          </div>

        </section>
      </main>

      {toast && (
        <div
          onClick={() => setToast(null)}
          className="fixed bottom-4 right-4 glass rounded-xl px-4 py-3 text-sm cursor-pointer animate-fadein"
        >
          {toast}
        </div>
      )}
    </div>
  )
}
