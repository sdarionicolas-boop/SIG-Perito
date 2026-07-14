import os
import sys
import math
import numpy as np
import pandas as pd
from scipy.signal import find_peaks

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# --- Configuration ---
S2_CSV = "data/serie_temporal_s2_hybris.csv"
S1_CSV = "data/serie_temporal_s1_hybris.csv"
OUTPUT_PRED_CSV = "data/predicciones_practicas_lotes.csv"

# Ventana de campaña y durmiente
CAMPANA_INICIO = pd.Timestamp("2025-10-01")
CAMPANA_FIN = pd.Timestamp("2026-05-31")

def normalize_percentiles(data, lower_percentile=0.02, upper_percentile=0.98):
    """Normalize a series using the 2nd and 98th percentiles and clip to [0,1]."""
    non_nan_data = data[~np.isnan(data)]
    if len(non_nan_data) == 0:
        return data
    
    percentiles = np.percentile(non_nan_data, [lower_percentile * 100, upper_percentile * 100])
    p_min, p_max = percentiles[0], percentiles[1]
    
    if p_max == p_min:
        return np.zeros_like(data)
        
    normalized = (data - p_min) / (p_max - p_min)
    return np.clip(normalized, 0.0, 1.0)

def daily_index_with_contributions_vectorized(
    s2df: pd.DataFrame,
    s1df: pd.DataFrame,
    maxDiff: int = 12,
    bandS2: str = 'BSI',
    bandS1: str = 'VH_VV',
    smoothing: int = 30
) -> pd.DataFrame:
    """Fuses S2 and S1 indices using a daily temporally weighted mean."""
    # Ensure datetimes
    s1 = s1df.copy()
    s2 = s2df.copy()
    s1['fecha'] = pd.to_datetime(s1['fecha'])
    s2['fecha'] = pd.to_datetime(s2['fecha'])

    start_date = min(s1['fecha'].min(), s2['fecha'].min())
    end_date = max(s1['fecha'].max(), s2['fecha'].max())
    daily_dates = pd.date_range(start=start_date, end=end_date, freq='D')

    daily_np = daily_dates.values.astype('datetime64[D]')
    s1_dates_np = s1['fecha'].values.astype('datetime64[D]')
    s2_dates_np = s2['fecha'].values.astype('datetime64[D]')

    s1_vals = s1[bandS1].values.astype(float) if bandS1 in s1.columns else np.full(len(s1), np.nan)
    s2_vals = s2[bandS2].values.astype(float) if bandS2 in s2.columns else np.full(len(s2), np.nan)

    n_days = daily_np.shape[0]

    # Handle S1 difference matrix
    if s1_dates_np.size > 0:
        s1_diff = np.abs((daily_np[:, None] - s1_dates_np[None, :]).astype('timedelta64[D]').astype(int))
        s1_notnan = ~np.isnan(s1_vals)
        s1_valid_mask = (s1_diff <= maxDiff) & s1_notnan[None, :]
        s1_weights = np.where(s1_valid_mask, 1.0 / (s1_diff + 1), 0.0)
        s1_num = (s1_weights * s1_vals[None, :]).sum(axis=1)
        s1_wsum = s1_weights.sum(axis=1)
    else:
        s1_num = np.zeros(n_days, dtype=float)
        s1_wsum = np.zeros(n_days, dtype=float)

    # Handle S2 difference matrix
    if s2_dates_np.size > 0:
        s2_diff = np.abs((daily_np[:, None] - s2_dates_np[None, :]).astype('timedelta64[D]').astype(int))
        s2_notnan = ~np.isnan(s2_vals)
        s2_valid_mask = (s2_diff <= maxDiff) & s2_notnan[None, :]
        s2_weights = np.where(s2_valid_mask, 1.0 / (s2_diff + 1), 0.0)
        s2_num = (s2_weights * s2_vals[None, :]).sum(axis=1)
        s2_wsum = s2_weights.sum(axis=1)
    else:
        s2_num = np.zeros(n_days, dtype=float)
        s2_wsum = np.zeros(n_days, dtype=float)

    total_weight = s1_wsum + s2_wsum

    with np.errstate(divide='ignore', invalid='ignore'):
        daily_index_arr = (s1_num + s2_num) / total_weight
        daily_index_arr = np.where(total_weight > 0, daily_index_arr, np.nan)

        s1_contrib_arr = np.where(total_weight > 0, s1_wsum / total_weight, 0.0)
        s2_contrib_arr = np.where(total_weight > 0, s2_wsum / total_weight, 0.0)

    out_df = pd.DataFrame({
        'fecha': daily_dates,
        'daily_index': daily_index_arr,
        's1_contribution': s1_contrib_arr,
        's2_contribution': s2_contrib_arr
    })

    # Centered rolling mean smoothing
    out_df['daily_index_smooth'] = out_df['daily_index'].rolling(
        window=smoothing, min_periods=1, center=True
    ).mean()

    return out_df

def calculate_hybris(s1_df, s2_df, bandS2='BSI', bandS1='VH_VV', maxDiff=12, smoothing=30):
    """Calculates normalized bands, daily fusion, and inverts BSI to resemble vegetation index."""
    s1 = s1_df.copy()
    s2 = s2_df.copy()

    # Normalization
    s1[bandS1] = normalize_percentiles(s1[bandS1].values)
    s2[bandS2] = normalize_percentiles(s2[bandS2].values)

    # Fusion
    hybris = daily_index_with_contributions_vectorized(
        s2, s1, maxDiff=maxDiff, bandS2=bandS2, bandS1=bandS1, smoothing=smoothing
    )

    if bandS2 == 'BSI':
        # Invert index to resemble greenness (so soil exposure corresponds to valleys)
        hybris['daily_index'] = 1.0 - hybris['daily_index']
        hybris['daily_index_smooth'] = 1.0 - hybris['daily_index_smooth']

    return hybris

def find_practices(hybris, min_prominence=0.08, min_distance=15):
    """Finds sowing (valley in smooth), harvest (valley in rough), and tillage (winter valleys)."""
    # Flip the time series so valleys become peaks
    smooth_series = -hybris["daily_index_smooth"].values
    rough_series = -hybris["daily_index"].values

    # Find smooth valleys (Sowing candidates)
    sow_idxs, sow_props = find_peaks(smooth_series, distance=min_distance, prominence=min_prominence)
    sow_proms = sow_props.get("prominences", [0.0]*len(sow_idxs))

    # Find rough valleys (Harvest and Tillage candidates)
    rough_idxs, rough_props = find_peaks(rough_series, distance=min_distance, prominence=min_prominence)
    rough_proms = rough_props.get("prominences", [0.0]*len(rough_idxs))

    # Find peaks of vegetation vigor (maximums)
    peak_idxs, peak_props = find_peaks(hybris["daily_index_smooth"].values, distance=min_distance, prominence=min_prominence)
    
    # Store events
    events = []
    
    # Check if we have a peak of season
    if len(peak_idxs) > 0:
        # Take the maximum peak as the main peak of season
        main_peak_idx = peak_idxs[np.argmax(hybris["daily_index_smooth"].values[peak_idxs])]
        peak_date = hybris["fecha"].iloc[main_peak_idx]
        peak_val = hybris["daily_index_smooth"].iloc[main_peak_idx]
        
        events.append({
            "fecha": peak_date,
            "evento": "pico_vigor",
            "valor": round(float(peak_val), 4),
            "prominencia": 1.0
        })
        
        # A. SOWING: last smooth valley before the peak of season
        sows_before = [idx for idx in sow_idxs if idx < main_peak_idx]
        if sows_before:
            sow_idx = sows_before[-1]
            sow_date = hybris["fecha"].iloc[sow_idx]
            sow_val = hybris["daily_index_smooth"].iloc[sow_idx]
            sow_prom = sow_proms[list(sow_idxs).index(sow_idx)]
            events.append({
                "fecha": sow_date,
                "evento": "siembra",
                "valor": round(float(sow_val), 4),
                "prominencia": round(float(sow_prom), 4)
            })
            
        # B. HARVEST: first rough valley after the peak of season
        harvs_after = [idx for idx in rough_idxs if idx > main_peak_idx]
        if harvs_after:
            harv_idx = harvs_after[0]
            harv_date = hybris["fecha"].iloc[harv_idx]
            harv_val = hybris["daily_index"].iloc[harv_idx]
            harv_prom = rough_proms[list(rough_idxs).index(harv_idx)]
            events.append({
                "fecha": harv_date,
                "evento": "cosecha",
                "valor": round(float(harv_val), 4),
                "prominencia": round(float(harv_prom), 4)
            })
            
            # C. TILLAGE (Laboreo invernal): any valleys in the rough series during
            # the winter period (June to September/October, post-harvest)
            # Typically, in this dataset, winter is after the harvest date (which is usually in April/May)
            tillages = [idx for idx in rough_idxs if idx > harv_idx]
            for idx in tillages:
                till_date = hybris["fecha"].iloc[idx]
                # Filter for winter period (June to October 2026)
                if till_date.month in (6, 7, 8, 9, 10):
                    till_val = hybris["daily_index"].iloc[idx]
                    till_prom = rough_proms[list(rough_idxs).index(idx)]
                    events.append({
                        "fecha": till_date,
                        "evento": "laboreo_invernal",
                        "valor": round(float(till_val), 4),
                        "prominencia": round(float(till_prom), 4)
                    })
    
    return events

def main():
    print("=" * 78)
    print("🌾 PIPELINE DE ANÁLISIS DE COSECHA Y LABOREO (HYBRIS-LIKE)")
    print("=" * 78)

    if not os.path.exists(S2_CSV) or not os.path.exists(S1_CSV):
        print("[ERROR] CSVs not found in data/. Run extractor_hybris.py first.")
        return

    df_s2_all = pd.read_csv(S2_CSV)
    df_s1_all = pd.read_csv(S1_CSV)
    
    df_s2_all["fecha"] = pd.to_datetime(df_s2_all["fecha"])
    df_s1_all["fecha"] = pd.to_datetime(df_s1_all["fecha"])

    all_predictions = []

    for lote_id in df_s2_all["lote_id"].unique():
        print(f"\nAnalyzing Lot: {lote_id}...")
        
        s2_lote = df_s2_all[df_s2_all["lote_id"] == lote_id]
        s1_lote = df_s1_all[df_s1_all["lote_id"] == lote_id]
        
        if s1_lote.empty or s2_lote.empty:
            print("   [skipped] Missing S1 or S2 observations for this lot.")
            continue
            
        # Select descending orbit for radar consistency as in original pipeline
        s1_desc = s1_lote[s1_lote["orbita"] == "DESCENDING"]
        if s1_desc.empty:
            s1_desc = s1_lote
            
        # 1. Calculate HyBRIS daily index
        hybris = calculate_hybris(s1_desc, s2_lote, bandS2='BSI', bandS1='VH_VV')
        print(f"   -> Calculated daily HyBRIS index ({len(hybris)} daily records).")
        
        # 2. Find practices (Sowing, Peak, Harvest, Tillage)
        events = find_practices(hybris)
        print(f"   -> Detected {len(events)} agricultural events.")
        
        for ev in events:
            print(f"      🔹 {ev['evento'].upper()}: {ev['fecha'].strftime('%Y-%m-%d')} (value: {ev['valor']:.3f}, prom: {ev['prominencia']:.3f})")
            all_predictions.append({
                "lote_id": lote_id,
                "fecha": ev["fecha"].strftime('%Y-%m-%d'),
                "evento": ev["evento"],
                "valor": ev["valor"],
                "prominencia": ev["prominencia"]
            })

    if all_predictions:
        df_pred = pd.DataFrame(all_predictions)
        df_pred.to_csv(OUTPUT_PRED_CSV, index=False)
        print("\n" + "=" * 78)
        print(f"🎉 SUCCESS! Predictions saved to: {OUTPUT_PRED_CSV}")
        print("=" * 78)
    else:
        print("\n[WARN] No agricultural events were detected.")

if __name__ == "__main__":
    main()
