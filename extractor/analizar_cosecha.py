import os
import sys
import argparse
import pandas as pd
import numpy as np

# Configure standard output to use UTF-8 to prevent encoding errors on Windows cp1252
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# --- Parametros del modelo de mezcla espectral ---
INPUT_CSV = "serie_temporal_lotes.csv"
SAR_CSV = "serie_temporal_sar_lotes.csv"
OUTPUT_CSV = "progreso_cosecha_lotes.csv"
RESUMEN_CSV = "resumen_fusion_cosecha.csv"

# Ventana de campana (pico de vigor): se busca el NDVI maximo aqui (0% cosecha)
CAMPANA_INICIO = pd.Timestamp("2025-11-01")
CAMPANA_FIN = pd.Timestamp("2026-03-31")

# Ventana de cosecha escalonada: se calcula el perfil de avance y el NDVI minimo (100% cosecha)
COSECHA_INICIO = pd.Timestamp("2026-04-01")
COSECHA_FIN = pd.Timestamp("2026-05-31")

# Umbrales para detectar inicio/fin de cosecha
UMBRAL_INICIO = 5.0    # progreso > 5%  -> arranco la cosecha
UMBRAL_FIN = 95.0      # progreso > 95% -> cosecha practicamente terminada

# Rango minimo de NDVI para considerar valida la senal de cosecha
RANGO_MINIMO_NDVI = 0.10

# Umbral de "despike": una caida de NDVI mayor a este valor respecto de AMBOS
# vecinos temporales, que ademas rebota, se interpreta como ruido orbital
# (nube/sombra residual) y no como cosecha. Fisica del problema: la cosecha real
# hace caer el NDVI y lo mantiene bajo; una nube lo hace caer y rebotar.
DESPIKE_UMBRAL = 0.15

# --- Parametros de la fusion con radar (Sentinel-1 / RVI) ---
# El NDVI mide verdor (cae con la SENESCENCIA: la planta se seca). El RVI mide
# estructura/rugosidad (cae solo cuando se REMUEVE el cultivo: arrancado +
# trillado = lote despejado). Por eso el RVI confirma la cosecha FISICA real,
# que el NDVI por si solo confunde con el secado foliar previo.
# Calibrado con datos 2026: RVI intacto ~0.83-1.17, RVI lote despejado ~0.35-0.44.
UMBRAL_RVI_COSECHA = 0.60   # RVI por debajo de esto (sostenido) = lote despejado


def despike_ndvi(valores, umbral=DESPIKE_UMBRAL):
    """Elimina caidas transitorias de NDVI (nubes/sombras residuales).

    Un punto interior se considera spike si esta por debajo de AMBOS vecinos
    temporales en mas de 'umbral' (es decir, cae y rebota). Se reemplaza por el
    menor de sus dos vecinos (criterio conservador). Las caidas REALES por
    cosecha no rebotan -> el vecino siguiente sigue bajo -> no se tocan. Los
    extremos de la serie nunca se modifican (una caida final real se conserva).
    """
    vals = list(valores)
    out = vals[:]
    for i in range(1, len(vals) - 1):
        prev_v, cur, next_v = vals[i - 1], vals[i], vals[i + 1]
        if (prev_v - cur) > umbral and (next_v - cur) > umbral:
            out[i] = min(prev_v, next_v)
    return out


def cargar_sar(path):
    """Carga la serie SAR (Sentinel-1 / RVI) indexada por lote. None si no existe.

    Si hay varias orbitas se usa la mas poblada (en estos datos solo DESCENDING).
    """
    if not os.path.exists(path):
        return None
    sar = pd.read_csv(path)
    sar["fecha"] = pd.to_datetime(sar["fecha"])
    if "orbita" in sar.columns and sar["orbita"].nunique() > 1:
        orbita_principal = sar["orbita"].value_counts().idxmax()
        sar = sar[sar["orbita"] == orbita_principal]
    return sar.sort_values(["lote_id", "fecha"]).reset_index(drop=True)


def detectar_cosecha_sar(sar_lote):
    """Detecta la fecha de cosecha FISICA (despeje del lote) por caida sostenida de RVI.

    El radar no tiene spikes de nube, asi que una caida de RVI por debajo del
    umbral que se MANTIENE baja (no rebota a valores de estructura intacta) marca
    la remocion fisica del cultivo. Devuelve dict con la fecha de despeje y los
    extremos de RVI, o None si no hay datos suficientes.
    """
    sar_lote = sar_lote.sort_values("fecha").reset_index(drop=True)

    campana = sar_lote[(sar_lote["fecha"] >= CAMPANA_INICIO) & (sar_lote["fecha"] <= CAMPANA_FIN)]
    if campana.empty:
        campana = sar_lote[sar_lote["fecha"] < COSECHA_INICIO]
    rvi_peak = float(campana["rvi_medio"].max()) if not campana.empty else float(sar_lote["rvi_medio"].max())

    cosecha = sar_lote[(sar_lote["fecha"] >= COSECHA_INICIO) & (sar_lote["fecha"] <= COSECHA_FIN)].reset_index(drop=True)
    if cosecha.empty:
        return None
    rvi_floor = float(cosecha["rvi_medio"].min())

    # Primer cruce sostenido por debajo del umbral dentro de la ventana de cosecha
    fecha_cosecha = None
    for i in range(len(cosecha)):
        if cosecha.at[i, "rvi_medio"] < UMBRAL_RVI_COSECHA:
            es_ultima = (i == len(cosecha) - 1)
            siguiente_baja = (not es_ultima) and (cosecha.at[i + 1, "rvi_medio"] < UMBRAL_RVI_COSECHA)
            if es_ultima or siguiente_baja:
                fecha_cosecha = cosecha.at[i, "fecha"]
                break

    return {"rvi_peak": rvi_peak, "rvi_floor": rvi_floor, "fecha_cosecha": fecha_cosecha}


def calcular_progreso_lote(lote_df):
    """Calcula la serie de avance de cosecha (%) para un lote.

    Modelo: mezcla lineal suelo desnudo vs. vegetacion verde.
      P_bruto = (NDVI_max - NDVI_t) / (NDVI_max - NDVI_min) * 100
    Acotado a [0, 100] y forzado monotono creciente (running maximum).

    Devuelve (lote_df_enriquecido, info) o (None, motivo) si no es valido.
    """
    lote_df = lote_df.sort_values("fecha").reset_index(drop=True)

    # 0. Despike robusto: neutraliza caidas transitorias de NDVI por ruido
    # orbital. El NDVI crudo se conserva en 'ndvi_medio'; el progreso se calcula
    # sobre 'ndvi_suave'.
    lote_df["ndvi_suave"] = despike_ndvi(lote_df["ndvi_medio"].tolist())

    # 1. Pico de vigor: NDVI maximo dentro de la ventana de campana (0% cosecha)
    campana = lote_df[(lote_df["fecha"] >= CAMPANA_INICIO) & (lote_df["fecha"] <= CAMPANA_FIN)]
    if campana.empty:
        # Fallback: maximo sobre toda la serie previa a la cosecha
        campana = lote_df[lote_df["fecha"] < COSECHA_INICIO]
    if campana.empty:
        return None, "sin datos en ventana de campana"

    idx_max = campana["ndvi_suave"].idxmax()
    ndvi_max = lote_df.at[idx_max, "ndvi_suave"]
    fecha_max = lote_df.at[idx_max, "fecha"]

    # 2. Suelo desnudo / rastrojo: NDVI minimo dentro de la ventana de cosecha (100% cosecha)
    cosecha = lote_df[(lote_df["fecha"] >= COSECHA_INICIO) & (lote_df["fecha"] <= COSECHA_FIN)]
    if cosecha.empty:
        return None, "sin datos en ventana de cosecha (abr-may 2026)"

    idx_min = cosecha["ndvi_suave"].idxmin()
    ndvi_min = lote_df.at[idx_min, "ndvi_suave"]
    fecha_min = lote_df.at[idx_min, "fecha"]

    rango = ndvi_max - ndvi_min
    if rango < RANGO_MINIMO_NDVI:
        return None, f"rango NDVI insuficiente ({rango:.3f}) - sin senal clara de cosecha"

    # 3. Progreso bruto por observacion (solo desde el pico en adelante)
    bruto = []
    for _, row in lote_df.iterrows():
        if row["fecha"] < fecha_max:
            bruto.append(0.0)
        else:
            p = (ndvi_max - row["ndvi_suave"]) / rango * 100.0
            bruto.append(min(100.0, max(0.0, p)))

    # 4. Restriccion de crecimiento monotono (running maximum)
    running = 0.0
    monotono = []
    for p in bruto:
        running = max(running, p)
        monotono.append(round(running, 1))

    lote_df["pct_cosechado"] = monotono
    lote_df["pct_restante"] = [round(100.0 - p, 1) for p in monotono]

    info = {
        "ndvi_max": ndvi_max, "fecha_max": fecha_max,
        "ndvi_min": ndvi_min, "fecha_min": fecha_min,
        "rango": rango,
    }
    return lote_df, info


def cruzar_umbral(lote_df, umbral):
    """Primera fecha en la que el avance acumulado supera 'umbral' (%). None si nunca."""
    cruce = lote_df[lote_df["pct_cosechado"] > umbral]
    if cruce.empty:
        return None
    return cruce.iloc[0]["fecha"]


def progreso_en_fecha(lote_df, fecha_corte):
    """Avance acumulado (%) a la fecha de corte: ultima observacion en o antes de T."""
    previas = lote_df[lote_df["fecha"] <= fecha_corte]
    if previas.empty:
        return 0.0
    return float(previas.iloc[-1]["pct_cosechado"])


def analizar_cosecha(csv_path, fecha_corte):
    if not os.path.exists(csv_path):
        print(f"[ERROR] No se encontro el CSV de origen: {csv_path}")
        return

    df = pd.read_csv(csv_path)
    df["fecha"] = pd.to_datetime(df["fecha"])
    df = df.sort_values(["lote_id", "fecha"]).reset_index(drop=True)

    sar = cargar_sar(SAR_CSV)  # None si no hay serie SAR -> degrada a solo-optico

    registros = []
    resumen = []

    print("=" * 78)
    print("ESTIMACION DE PROGRESO DE COSECHA SATELITAL (MANI) - FUSION OPTICO + RADAR")
    print(f"Fecha de corte consultada: {fecha_corte.strftime('%Y-%m-%d')}")
    print("NDVI = secado foliar (senescencia) | RVI (Sentinel-1) = cosecha fisica")
    if sar is None:
        print("[AVISO] No se encontro serie SAR -> solo señal optica (NDVI).")
    print("=" * 78)

    for lote in df["lote_id"].unique():
        lote_df = df[df["lote_id"] == lote].copy()
        resultado, info = calcular_progreso_lote(lote_df)

        print(f"\nLote: {lote}")
        if resultado is None:
            print(f"   [omitido] {info}")
            continue

        lote_df = resultado
        f_inicio = cruzar_umbral(lote_df, UMBRAL_INICIO)
        f_fin = cruzar_umbral(lote_df, UMBRAL_FIN)
        p_corte_ndvi = progreso_en_fecha(lote_df, fecha_corte)

        # --- Senal de radar (cosecha fisica) ---
        sar_res = None
        if sar is not None:
            sar_lote = sar[sar["lote_id"] == lote]
            if not sar_lote.empty:
                sar_res = detectar_cosecha_sar(sar_lote)

        fecha_cosecha_sar = sar_res["fecha_cosecha"] if sar_res else None
        # Avance fisico confirmado por radar: 0% antes del despeje, 100% desde el despeje
        if fecha_cosecha_sar is not None:
            pct_fisico_corte = 100.0 if fecha_corte >= fecha_cosecha_sar else 0.0
        else:
            pct_fisico_corte = None  # sin confirmacion radar disponible

        # --- Reporte ---
        print("   [OPTICO/NDVI] secado foliar (precursor de cosecha)")
        print(f"      Pico de vigor (0%)   : NDVI {info['ndvi_max']:.3f} el {info['fecha_max'].strftime('%Y-%m-%d')}")
        print(f"      Suelo desnudo (100%) : NDVI {info['ndvi_min']:.3f} el {info['fecha_min'].strftime('%Y-%m-%d')}")
        ini_txt = f_inicio.strftime('%Y-%m-%d') if f_inicio is not None else "sin inicio"
        fin_txt = f_fin.strftime('%Y-%m-%d') if f_fin is not None else "no completado"
        print(f"      Inicio secado (>5%)  : {ini_txt}   Fin secado (>95%): {fin_txt}")
        print(f"      Secado foliar al {fecha_corte.strftime('%Y-%m-%d')}: {p_corte_ndvi:.1f}%")

        print("   [RADAR/RVI] cosecha fisica confirmada (arrancado + trillado)")
        if sar_res is None:
            print("      (sin datos SAR para este lote)")
        elif fecha_cosecha_sar is None:
            print(f"      RVI pico {sar_res['rvi_peak']:.2f} -> piso {sar_res['rvi_floor']:.2f}: "
                  f"sin despeje confirmado en el periodo")
        else:
            print(f"      RVI pico {sar_res['rvi_peak']:.2f} -> piso {sar_res['rvi_floor']:.2f}")
            print(f"      >>> Lote DESPEJADO (cosechado) el: {fecha_cosecha_sar.strftime('%Y-%m-%d')}")

        if pct_fisico_corte is not None:
            estado = "YA COSECHADO" if pct_fisico_corte >= 100 else "AUN EN PIE (secando, no cosechado)"
            print(f"   ==> AL {fecha_corte.strftime('%Y-%m-%d')}: cosecha fisica {pct_fisico_corte:.0f}% -> {estado}")
            print(f"       Restante por cosechar despues de esa fecha: {100.0 - pct_fisico_corte:.0f}%")
        else:
            print(f"   ==> AL {fecha_corte.strftime('%Y-%m-%d')}: sin confirmacion radar; "
                  f"referencia optica (secado) {p_corte_ndvi:.1f}%")

        resumen.append({
            "lote_id": lote,
            "fecha_corte": fecha_corte.strftime('%Y-%m-%d'),
            "ndvi_inicio_secado": ini_txt,
            "ndvi_fin_secado": fin_txt,
            "pct_secado_ndvi_al_corte": round(p_corte_ndvi, 1),
            "fecha_cosecha_fisica_sar": fecha_cosecha_sar.strftime('%Y-%m-%d') if fecha_cosecha_sar is not None else "",
            "pct_cosechado_fisico_al_corte": pct_fisico_corte if pct_fisico_corte is not None else "",
            "pct_restante_al_corte": (100.0 - pct_fisico_corte) if pct_fisico_corte is not None else "",
        })

        # --- Perfil diario fusionado ---
        for _, r in lote_df.iterrows():
            if fecha_cosecha_sar is not None:
                pct_sar = 100.0 if r["fecha"] >= fecha_cosecha_sar else 0.0
            else:
                pct_sar = ""
            registros.append({
                "lote_id": r["lote_id"],
                "fecha": r["fecha"].strftime('%Y-%m-%d'),
                "ndvi": round(float(r["ndvi_medio"]), 4),
                "ndvi_suavizado": round(float(r["ndvi_suave"]), 4),
                "pct_secado_ndvi": r["pct_cosechado"],
                "pct_cosechado_sar": pct_sar,
                "pct_restante_sar": (100.0 - pct_sar) if pct_sar != "" else "",
            })

    if registros:
        pd.DataFrame(registros).to_csv(OUTPUT_CSV, index=False, encoding="utf-8")
        pd.DataFrame(resumen).to_csv(RESUMEN_CSV, index=False, encoding="utf-8")
        print("\n" + "=" * 78)
        print(f"Perfil diario fusionado guardado en : {OUTPUT_CSV}")
        print("  Columnas: lote_id, fecha, ndvi, ndvi_suavizado, pct_secado_ndvi,")
        print("            pct_cosechado_sar, pct_restante_sar")
        print(f"Resumen por lote guardado en        : {RESUMEN_CSV}")
        print("=" * 78)
    else:
        print("\n[ADVERTENCIA] Ningun lote genero perfil de cosecha valido.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Estima el avance (%) de cosecha de mani por lote a partir de la serie temporal de NDVI."
    )
    parser.add_argument(
        "fecha_corte", nargs="?", default="2026-04-15",
        help="Fecha de corte YYYY-MM-DD (por defecto 2026-04-15)."
    )
    parser.add_argument("--csv", default=INPUT_CSV, help="Ruta del CSV de serie temporal.")
    args = parser.parse_args()

    try:
        fecha_corte = pd.Timestamp(args.fecha_corte)
    except Exception:
        print(f"[ERROR] Fecha de corte invalida: {args.fecha_corte} (use YYYY-MM-DD)")
        sys.exit(1)

    analizar_cosecha(args.csv, fecha_corte)
