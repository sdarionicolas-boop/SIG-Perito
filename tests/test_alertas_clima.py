# tests/test_alertas_clima.py
"""Suite para evaluar_alertas_tormenta: demos, umbrales reales y robustez."""
import sqlite3
import pytest
import app.services.alertas_clima as ac


# ---------- Fixtures ----------
@pytest.fixture
def db_temp(tmp_path, monkeypatch):
    """DB SQLite temporal con la tabla lotes; parchea DB_PATH del módulo."""
    db = tmp_path / "test.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE lotes (id INTEGER PRIMARY KEY, nombre TEXT, "
                 "centroide_lat REAL, centroide_lon REAL)")
    conn.executemany(
        "INSERT INTO lotes (id, nombre, centroide_lat, centroide_lon) VALUES (?,?,?,?)",
        [(1, "La Esperanza Lote 3", -34.6, -60.5),      # real
         (2, "Campo DEMO 2 Sur", -35.0, -61.0),         # demo 2
         (3, "Parcela DEMO 1", -35.0, -61.0),           # demo 1
         (4, "Lote sin coords", None, None)])           # sin lat/lon
    conn.commit(); conn.close()
    
    # Parcheamos la ruta de la base de datos en todos los módulos relevantes
    monkeypatch.setattr(ac, "DB_PATH", str(db))
    monkeypatch.setattr("app.database.DB_PATH", str(db))
    monkeypatch.setattr("app.config.DB_PATH", str(db))
    return db


class FakeResp:
    def __init__(self, payload, ok=True, status=200):
        self._p, self.ok, self.status_code = payload, ok, status
    def json(self): return self._p


def _hourly(cape=None, gusts=None, precip=None, n=3):
    """Construye un payload horario Open-Meteo con n horas."""
    base = ["2026-07-03T14:00", "2026-07-03T18:00", "2026-07-04T02:00"][:n]
    return {"hourly": {
        "time": base,
        "cape":   cape   if cape   is not None else [0.0] * n,
        "wind_gusts_10m": gusts if gusts is not None else [0.0] * n,
        "precipitation":  precip if precip is not None else [0.0] * n,
    }}


def patch_get(monkeypatch, payload=None, exc=None, ok=True, status=200):
    def _fake(url, **kw):
        if exc: raise exc
        return FakeResp(payload, ok=ok, status=status)
    monkeypatch.setattr(ac.requests, "get", _fake)


def _ensemble(cape=None, gusts=None, precip=None, n_members=31, n_hours=3):
    """Payload del ensemble GEFS de Open-Meteo con claves '<var>_memberNN'.

    cape/gusts/precip son listas de 'n_members' series (cada una de largo
    n_hours). El miembro 0 va en la clave 'pelada' (control) y el resto como
    '<var>_memberNN', replicando el formato real de Open-Meteo.
    """
    base = ["2026-07-03T14:00", "2026-07-03T18:00", "2026-07-04T02:00"][:n_hours]
    hourly = {"time": base}

    def _add(var, data):
        if data is None:
            data = [[0.0] * n_hours for _ in range(n_members)]
        for k, serie in enumerate(data):
            hourly[var if k == 0 else f"{var}_member{k:02d}"] = serie

    _add("cape", cape)
    _add("wind_gusts_10m", gusts)
    _add("precipitation", precip)
    return {"hourly": hourly}


# ---------- Inyección de demos ----------
def test_demo2_dispara_dos_alertas(db_temp, monkeypatch):
    patch_get(monkeypatch, exc=AssertionError("no debe llamar a la red"))
    r = ac.evaluar_alertas_tormenta(2)
    assert r["hay_alertas"] and r["fuente"] == "simulado"
    assert {a["tipo"] for a in r["alertas_lista"]} == {"Riesgo de Granizo",
                                                       "Riesgo de Vientos Fuertes"}

def test_demo1_una_alerta(db_temp, monkeypatch):
    patch_get(monkeypatch, exc=AssertionError("no debe llamar a la red"))
    r = ac.evaluar_alertas_tormenta(3)
    assert len(r["alertas_lista"]) == 1 and r["fuente"] == "simulado"


# ---------- Lotes reales: umbrales ----------
def test_real_sin_alertas(db_temp, monkeypatch):
    patch_get(monkeypatch, payload=_hourly())          # todo en cero
    r = ac.evaluar_alertas_tormenta(1)
    assert r["hay_alertas"] is False and r["fuente"] == "real"

def test_real_granizo_por_cape(db_temp, monkeypatch):
    patch_get(monkeypatch, payload=_hourly(cape=[0, 2500, 0]))
    r = ac.evaluar_alertas_tormenta(1)
    tipos = {a["tipo"] for a in r["alertas_lista"]}
    assert "Riesgo de Granizo" in tipos

@pytest.mark.parametrize("cape", [1999.9, 2000.0])
def test_umbral_cape_borde(db_temp, monkeypatch, cape):
    patch_get(monkeypatch, payload=_hourly(cape=[cape, 0, 0]))
    r = ac.evaluar_alertas_tormenta(1)
    esperado = cape >= ac.UMBRAL_CAPE
    assert r["hay_alertas"] is esperado

def test_real_viento_y_lluvia(db_temp, monkeypatch):
    patch_get(monkeypatch, payload=_hourly(gusts=[60, 0, 0], precip=[20, 0, 0]))
    tipos = {a["tipo"] for a in ac.evaluar_alertas_tormenta(1)["alertas_lista"]}
    assert tipos == {"Riesgo de Vientos Fuertes", "Riesgo de Lluvia Intensa"}


# ---------- Robustez (los bugs de la auditoría) ----------
def test_valores_none_no_rompen(db_temp, monkeypatch):
    patch_get(monkeypatch, payload=_hourly(cape=[None, 2500, None]))
    r = ac.evaluar_alertas_tormenta(1)          # no debe lanzar
    assert "Riesgo de Granizo" in {a["tipo"] for a in r["alertas_lista"]}

def test_lote_sin_coordenadas(db_temp, monkeypatch):
    patch_get(monkeypatch, exc=AssertionError("no debe llamar a la red"))
    r = ac.evaluar_alertas_tormenta(4)
    assert r["hay_alertas"] is False and r["fuente"] == "real"

def test_lote_inexistente_lanza_valueerror(db_temp):
    with pytest.raises(ValueError):
        ac.evaluar_alertas_tormenta(999)

def test_timeout_no_propaga_pero_marca_error(db_temp, monkeypatch):
    import requests
    patch_get(monkeypatch, exc=requests.exceptions.Timeout())
    r = ac.evaluar_alertas_tormenta(1)
    assert r["hay_alertas"] is False and "error" in r

def test_http_500_devuelve_error(db_temp, monkeypatch):
    patch_get(monkeypatch, payload={}, ok=False, status=500)
    r = ac.evaluar_alertas_tormenta(1)
    assert r["hay_alertas"] is False and r["error"] == "HTTP 500"

def test_arrays_de_distinta_longitud(db_temp, monkeypatch):
    payload = {"hourly": {"time": ["2026-07-03T14:00", "2026-07-03T18:00"],
                          "cape": [2500], "wind_gusts_10m": [0], "precipitation": [0]}}
    patch_get(monkeypatch, payload=payload)
    r = ac.evaluar_alertas_tormenta(1)          # con el fix, esto NO lanza IndexError
    assert "Riesgo de Granizo" in {a["tipo"] for a in r["alertas_lista"]}


# ---------- Fase 1: motor probabilístico GEFS (multi-miembro) ----------
def test_ensemble_probabilidad_granizo(db_temp, monkeypatch):
    # 6 de 10 miembros cruzan el umbral de CAPE -> 60%
    cape = [[2500.0, 0.0]] * 6 + [[0.0, 0.0]] * 4
    patch_get(monkeypatch, payload=_ensemble(cape=cape, n_members=10, n_hours=2))
    r = ac.evaluar_alertas_tormenta(1)
    granizo = next(a for a in r["alertas_lista"] if a["tipo"] == "Riesgo de Granizo")
    assert granizo["prob_pct"] == 60
    assert abs(granizo["probabilidad"] - 0.60) < 1e-9
    assert granizo["gravedad"] == "media"            # 0.40 <= 0.60 < 0.70


def test_ensemble_por_debajo_del_minimo_no_avisa(db_temp, monkeypatch):
    # 1 de 20 miembros -> 5% < PROB_MIN_AVISO (15%) -> sin alerta
    cape = [[2500.0]] + [[0.0]] * 19
    patch_get(monkeypatch, payload=_ensemble(cape=cape, n_members=20, n_hours=1))
    r = ac.evaluar_alertas_tormenta(1)
    assert r["hay_alertas"] is False


def test_ensemble_ordena_por_probabilidad(db_temp, monkeypatch):
    cape = [[2500.0]] * 9 + [[0.0]]          # 9/10 = 0.90 (granizo)
    gusts = [[60.0]] * 5 + [[0.0]] * 5       # 5/10 = 0.50 (viento)
    patch_get(monkeypatch, payload=_ensemble(cape=cape, gusts=gusts,
                                             n_members=10, n_hours=1))
    tipos = [a["tipo"] for a in ac.evaluar_alertas_tormenta(1)["alertas_lista"]]
    assert tipos[0] == "Riesgo de Granizo"           # el más probable primero
    assert "Riesgo de Vientos Fuertes" in tipos


def test_ensemble_gravedad_alta_sobre_70(db_temp, monkeypatch):
    cape = [[2500.0]] * 8 + [[0.0]] * 2      # 8/10 = 0.80 -> alta
    patch_get(monkeypatch, payload=_ensemble(cape=cape, n_members=10, n_hours=1))
    granizo = ac.evaluar_alertas_tormenta(1)["alertas_lista"][0]
    assert granizo["prob_pct"] == 80 and granizo["gravedad"] == "alta"


def test_ensemble_hora_critica_es_de_mayor_consenso(db_temp, monkeypatch):
    # hora 0 (14:00): cruzan 7 miembros; hora 1 (18:00): cruzan los 10
    cape = [[2500.0, 3000.0]] * 7 + [[0.0, 3000.0]] * 3
    patch_get(monkeypatch, payload=_ensemble(cape=cape, n_members=10, n_hours=2))
    granizo = next(a for a in ac.evaluar_alertas_tormenta(1)["alertas_lista"]
                   if a["tipo"] == "Riesgo de Granizo")
    assert "18:00" in granizo["fecha"]               # hora de máximo consenso


def test_ensemble_intensidad_es_mediana_del_pico(db_temp, monkeypatch):
    # picos por miembro: 2100,2200,...,2500 -> mediana 2300 J/kg en el mensaje
    cape = [[2100.0], [2200.0], [2300.0], [2400.0], [2500.0]]
    patch_get(monkeypatch, payload=_ensemble(cape=cape, n_members=5, n_hours=1))
    granizo = ac.evaluar_alertas_tormenta(1)["alertas_lista"][0]
    assert "2300 J/kg" in granizo["mensaje"]


def test_ensemble_sin_miembros_no_rompe(db_temp, monkeypatch):
    patch_get(monkeypatch, payload={"hourly": {"time": ["2026-07-03T14:00"]}})
    r = ac.evaluar_alertas_tormenta(1)               # sin variables -> sin datos
    assert r["hay_alertas"] is False and r["fuente"] == "real"
