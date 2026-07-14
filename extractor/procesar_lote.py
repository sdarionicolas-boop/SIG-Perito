import os
import sys
import glob
import re
import argparse
import subprocess
import numpy as np
import pandas as pd
import geopandas as gpd
import rasterio
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
from PIL import Image

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# Deliverables are written to the user's Desktop (per-lot folder).
DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")

def compile_animated_gif(lote_id, shp_path, umbral, tifs_dir):
    """Dynamically compiles the animated progress GIF for any lot."""
    print("\n🎬 COMPILING ANIMATED GIF...")
    
    # 1. Load shapefile geometry
    gdf = gpd.read_file(shp_path)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    elif gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs("EPSG:4326")

    # Find row matching lote_id
    if "Mani_2425_L" in lote_id:
        match = re.search(r"_L(\d+)", lote_id)
        lote_num_str = match.group(1) if match else ""
        row = None
        for i, r in gdf.iterrows():
            if str(r.get("ID", "")).strip() == lote_num_str.strip():
                row = r
                break
    elif "Soja_2425_L" in lote_id:
        match = re.search(r"_L(\d+)", lote_id)
        lote_num_str = match.group(1) if match else ""
        row = None
        for i, r in gdf.iterrows():
            if str(r.get("id", "")).strip() == lote_num_str.strip():
                row = r
                break
    else:
        match = re.search(r"Lote_(\d+)_", lote_id)
        if match:
            idx = int(match.group(1))
            row = gdf.iloc[idx]
        else:
            row = None
            for i, r in gdf.iterrows():
                camp = str(r.get("Nomb_campo", "")).strip().replace(" ", "_")
                if camp and camp.upper() in lote_id.upper():
                    row = r
                    break
            if row is None:
                print("[ERROR GIF] Geometry not found.")
                return

    geom = row["geometry"]

    # 2. Load the dynamic progress CSV
    report_csv = f"data/reportes/{lote_id}_progreso_cosecha.csv"
    if not os.path.exists(report_csv):
        print(f"[ERROR GIF] Report CSV not found: {report_csv}. Run analysis first.")
        return
        
    df_prog = pd.read_csv(report_csv)
    # Convert dates to string for matching
    df_prog["fecha"] = df_prog["fecha"].astype(str)

    # 3. Find generated mask files on Desktop
    desktop_folder = os.path.join(DESKTOP, f"Secuencia_Cosecha_{lote_id}")
    mask_files = glob.glob(os.path.join(desktop_folder, f"{lote_id}_*_mascara_cosecha.tif"))
    
    if not mask_files:
        print("[ERROR GIF] No mask files found on Desktop folder.")
        return

    records = []
    for filepath in mask_files:
        filename = os.path.basename(filepath)
        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", filename)
        if date_match:
            date_str = date_match.group(1)
            records.append((date_str, filepath))
            
    records = sorted(records, key=lambda x: x[0])

    temp_dir = os.path.join(desktop_folder, "temp_frames")
    os.makedirs(temp_dir, exist_ok=True)

    colors = ["#2ecc71", "#c0392b"] # Green (intact), Dark Red (harvested)
    cmap = ListedColormap(colors)
    frame_paths = []

    for idx_frame, (date_str, filepath) in enumerate(records):
        # Match with CSV row
        csv_row = df_prog[df_prog["fecha"] == date_str]
        if csv_row.empty:
            pct_val = 0.0
            ha_val = 0.0
            total_ha = 0.0
        else:
            pct_val = float(csv_row.iloc[0]["pct_cosechado"])
            ha_val = float(csv_row.iloc[0]["hectareas_cosechadas"])
            total_ha = float(csv_row.iloc[0]["hectareas_totales"])

        # Determine dynamic state description
        month = int(date_str.split("-")[1])
        if pct_val < 20.0:
            if month == 4:
                estado = "Cultivo Maduro (Pre-cosecha)"
            elif month == 5:
                estado = "Arrancado / Hilereado"
            else:
                estado = "Fase Vegetativa / Siembra"
        elif 20.0 <= pct_val < 50.0:
            if month == 6:
                estado = "Inicio de Laboreo Invernal"
            else:
                estado = "Cosecha Inicial / Hilera"
        elif 50.0 <= pct_val < 75.0:
            estado = "Trilla en Proceso"
        else: # >= 75%
            if month == 6:
                estado = "Suelo Labrado Post-cosecha"
            else:
                estado = "Trilla Finalizada"

        # Plot
        plt.figure(figsize=(8, 8), dpi=100)
        plt.gcf().patch.set_facecolor('#1e1e1e')
        ax = plt.subplot(111)
        ax.set_facecolor('#1e1e1e')
        
        with rasterio.open(filepath) as src:
            rvi_mask = src.read(1)
            extent = [src.bounds.left, src.bounds.right, src.bounds.bottom, src.bounds.top]
            ax.imshow(rvi_mask, cmap=cmap, extent=extent, vmin=0, vmax=1)
            
        gpd.GeoSeries([geom]).boundary.plot(ax=ax, color='#f1c40f', linewidth=2, zorder=5)
        ax.axis('off')
        
        # Text overlays
        plt.text(0.05, 0.93, f"COSECHA MANÍ - {lote_id}", color='#ffffff', fontsize=14, fontweight='bold', transform=ax.transAxes)
        plt.text(0.05, 0.88, f"Fecha: {date_str}", color='#f1c40f', fontsize=14, fontweight='bold', transform=ax.transAxes)
        plt.text(0.05, 0.83, f"Estado: {estado}", color='#ffffff', fontsize=11, transform=ax.transAxes)
        
        plt.text(0.05, 0.12, f"Avance: {pct_val:.1f}%", color='#e74c3c' if pct_val > 50 else '#2ecc71', fontsize=16, fontweight='bold', transform=ax.transAxes)
        plt.text(0.05, 0.07, f"Levantado: {ha_val:.2f} ha / {total_ha:.2f} ha", color='#cccccc', fontsize=11, transform=ax.transAxes)
        
        plt.text(0.70, 0.12, "■ En pie / Hilera", color='#2ecc71', fontsize=11, fontweight='bold', transform=ax.transAxes)
        plt.text(0.70, 0.07, "■ Cosechado / Trillado", color='#c0392b', fontsize=11, fontweight='bold', transform=ax.transAxes)
        
        plt.tight_layout()
        
        frame_path = os.path.join(temp_dir, f"frame_{idx_frame:02d}.png")
        plt.savefig(frame_path, facecolor='#1e1e1e', edgecolor='none', bbox_inches='tight')
        plt.close()
        frame_paths.append(frame_path)

    # Compile to GIF
    if frame_paths:
        images = [Image.open(f) for f in frame_paths]
        gif_dest1 = os.path.join(desktop_folder, "avance_cosecha.gif")
        gif_dest2 = os.path.join(DESKTOP, f"{lote_id}_avance_cosecha.gif")
        
        images[0].save(gif_dest1, format='GIF', append_images=images[1:], save_all=True, duration=1200, loop=0)
        images[0].save(gif_dest2, format='GIF', append_images=images[1:], save_all=True, duration=1200, loop=0)
        print(f"✅ GIF Animation compiled:")
        print(f"   -> {gif_dest1}")
        print(f"   -> {gif_dest2}")

        # Clean up
        for f in frame_paths:
            try:
                os.remove(f)
            except Exception:
                pass
        try:
            os.rmdir(temp_dir)
        except Exception:
            pass

def main():
    parser = argparse.ArgumentParser(description="Master Pipeline de Cosecha Maní (Óptico-Radar Híbrido)")
    parser.add_argument("--lote", type=str, required=True, help="Nombre del lote (búsqueda parcial)")
    parser.add_argument("--umbral", type=float, default=0.55, help="Fracción del pico de vigor: cosechado si RVI < umbral*pico (0-1, defecto 0.55)")
    parser.add_argument("--shp", type=str, default="data/lotes_mani.shp", help="Shapefile path")
    parser.add_argument("--s1_csv", type=str, default="data/serie_temporal_s1_hybris.csv", help="S1 statistics CSV path")
    parser.add_argument("--tifs_dir", type=str, default="data/tifs_procesados", help="Rasters download folder")
    parser.add_argument("--skip_download", action="store_true", help="Skip Processing API download step")
    args = parser.parse_args()

    print("=" * 80)
    print("🌾 MASTER RUNNER: PIPELINE AUTOMÁTICO DE COSECHA DE MANÍ")
    print("=" * 80)

    # Find matched lote_id first
    if not os.path.exists(args.s1_csv):
        print(f"[ERROR] Radar stats CSV not found: {args.s1_csv}. Please run extractor_hybris.py first.")
        return
        
    df_s1 = pd.read_csv(args.s1_csv)
    matched = [l for l in df_s1["lote_id"].unique() if args.lote.upper() in l.upper()]
    if not matched:
        print(f"[ERROR] Lot matching '{args.lote}' not found in stats CSV.")
        return
    lote_id = matched[0]
    print(f"🚀 Running pipeline for Lot: {lote_id}")

    # Step 1: Download TIFs (Process API)
    if not args.skip_download:
        print("\n[STEP 1] Running automatic Processing API downloader...")
        cmd_dl = [
            sys.executable,
            "extractor/descargar_tifs_automatico.py",
            "--lote_id", lote_id,
            "--shp", args.shp,
            "--s1_csv", args.s1_csv,
            "--output_dir", args.tifs_dir
        ]
        res = subprocess.run(cmd_dl)
        if res.returncode != 0:
            print("[ERROR] Download step failed. Halting pipeline.")
            return
    else:
        print("\n[STEP 1] Skipped download step (--skip_download).")

    # Step 2: Analyze progress pixel-by-pixel
    print("\n[STEP 2] Running pixel-by-pixel harvest progress analysis...")
    cmd_an = [
        sys.executable,
        "extractor/analizar_raster_cosecha.py",
        "--lote_id", lote_id,
        "--shp", args.shp,
        "--tifs_dir", args.tifs_dir,
        "--umbral", str(args.umbral)
    ]
    res = subprocess.run(cmd_an)
    if res.returncode != 0:
        print("[ERROR] Analysis step failed. Halting pipeline.")
        return

    # Step 3: Generate binary QGIS masks
    print("\n[STEP 3] Generating QGIS binary masks...")
    cmd_mask = [
        sys.executable,
        "extractor/generar_mascaras_qgis.py",
        "--lote_id", lote_id,
        "--shp", args.shp,
        "--tifs_dir", args.tifs_dir,
        "--umbral", str(args.umbral)
    ]
    res = subprocess.run(cmd_mask)
    if res.returncode != 0:
        print("[ERROR] Mask generation step failed. Halting pipeline.")
        return

    # Step 4: Compile animated GIF
    compile_animated_gif(lote_id, args.shp, args.umbral, args.tifs_dir)

    print("\n" + "=" * 80)
    print("🎉 PIPELINE COMPLETED SUCCESSFULLY FOR LOT:", lote_id)
    print("=" * 80)

if __name__ == "__main__":
    main()
