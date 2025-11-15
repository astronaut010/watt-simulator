# backend/app.py
import os
import io
import re
import math
import sqlite3
from datetime import datetime
from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
import numpy as np
import cv2
from PIL import Image
import pytesseract
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

# ensure tesseract binary location (Render installs it system-wide)
pytesseract.pytesseract.tesseract_cmd = "/usr/bin/tesseract"

# App where static frontend sits at ../frontend
app = Flask(__name__, static_folder="../frontend", static_url_path="")
CORS(app)

DB_FILE = "wattcompare.db"
CO2_FACTOR = 0.82  # kg CO2 per kWh

# --------- Serve frontend ----------
@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")

@app.route("/<path:path>")
def static_proxy(path):
    return send_from_directory(app.static_folder, path)

# --------- Database ----------
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS appliances (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        energy_kwh REAL,
        price REAL,
        energy_rate REAL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)
    conn.commit()
    conn.close()

def get_db():
    return sqlite3.connect(DB_FILE)

init_db()

# --------- Image preprocessing and OCR ----------
def preprocess_for_ocr(cv_img):
    # convert to gray, denoise, and adaptive threshold to help OCR
    gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
    # increase contrast
    gray = cv2.equalizeHist(gray)
    # denoise
    gray = cv2.bilateralFilter(gray, 9, 75, 75)
    # adaptive threshold
    th = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                               cv2.THRESH_BINARY, 15, 8)
    # morphological open to remove small noise
    kernel = np.ones((1,1), np.uint8)
    opened = cv2.morphologyEx(th, cv2.MORPH_OPEN, kernel)
    return opened

def run_tesseract_on_image(np_img):
    # try multiple PSMs to improve detection
    pil = Image.fromarray(np_img)
    configs = [
        "--oem 3 --psm 6",  # assume a block of text
        "--oem 3 --psm 11", # sparse text
        "--oem 3 --psm 3",  # fully automatic layout
    ]
    text = ""
    for cfg in configs:
        try:
            # include common language codes - installed via Aptfile
            out = pytesseract.image_to_string(pil, lang="eng+hin+tam+tel+spa+fra+deu+ita+por+rus+ara+jpn+kor", config=cfg)
            if out and len(out.strip()) > len(text):
                text = out
        except Exception:
            continue
    return text

def extract_kwh_from_text(text):
    if not text:
        return None, text
    txt = text.lower().replace(",", ".").replace("\n", " ")
    # look for number + unit (kwh, kw, w) with optional per month/day/year
    m = re.search(r"(\d+(?:\.\d+)?)\s*(kwh|kw|w)\s*(?:/|per)?\s*(year|yr|month|mo|day|d)?", txt)
    if not m:
        # sometimes label says "annual energy consumption 250 kwh"
        m2 = re.search(r"(annual|yearly|per year).{0,30}?(\d+(?:\.\d+)?)\s*(kwh|kw|w)", txt)
        if m2:
            val = float(m2.group(2))
            unit = m2.group(3)
            per = "year"
        else:
            return None, text
    else:
        val = float(m.group(1))
        unit = m.group(2)
        per = m.group(3) or "year"

    # normalize to kWh/year
    if unit == "w":
        # W assumed to be power; convert to kW then assume 3 hours/day usage as fallback
        kw = val / 1000.0
        annual = kw * 3 * 365
    elif unit == "kw":
        # kW to kWh/year - if period specified we'll adjust; assume 24h/day if not specified? safer: assume 24h if label is power
        if per in ["month", "mo"]:
            annual = val * 12
        elif per in ["day", "d"]:
            annual = val * 365
        else:
            # treat as power: assume 24h/day
            annual = val * 24 * 365 / 1.0
    else:  # kwh
        if per in ["month", "mo"]:
            annual = val * 12
        elif per in ["day", "d"]:
            annual = val * 365
        else:
            annual = val

    return round(float(annual), 2), text

def ocr_image_bytes(image_bytes):
    np_img = np.frombuffer(image_bytes, np.uint8)
    cv_img = cv2.imdecode(np_img, cv2.IMREAD_COLOR)
    if cv_img is None:
        return None, ""
    processed = preprocess_for_ocr(cv_img)
    text = run_tesseract_on_image(processed)
    aec, raw = extract_kwh_from_text(text)
    return aec, text

# --------- API Endpoints (namespace /api) ----------
@app.route("/api/ocr", methods=["POST"])
def api_ocr():
    if "image" not in request.files:
        return jsonify({"error": "No image file uploaded"}), 400
    f = request.files["image"]
    img_bytes = f.read()
    aec, raw_text = ocr_image_bytes(img_bytes)
    return jsonify({"estimated_kwh_per_year": aec, "raw_text": raw_text})

@app.route("/api/add_appliance", methods=["POST"])
def api_add_appliance():
    # Accept form-data (from frontend)
    name = request.form.get("name") or "Unnamed"
    price = float(request.form.get("price") or 0)
    energy_rate = float(request.form.get("energy_rate") or 0)
    # accept either provided numeric aec or try to extract from uploaded image
    aec = request.form.get("aec") or request.form.get("energy_kwh")
    if not aec and "image" in request.files:
        img_bytes = request.files["image"].read()
        aec, _ = ocr_image_bytes(img_bytes)
    try:
        energy_kwh = float(aec) if aec else None
    except:
        energy_kwh = None

    conn = get_db()
    cur = conn.cursor()
    cur.execute("INSERT INTO appliances (name, energy_kwh, price, energy_rate) VALUES (?, ?, ?, ?)",
                (name, energy_kwh, price, energy_rate))
    conn.commit()
    inserted_id = cur.lastrowid
    conn.close()
    return jsonify({"message": "saved", "id": inserted_id})

@app.route("/api/list_appliances", methods=["GET"])
def api_list_appliances():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, name, energy_kwh, price, energy_rate, created_at FROM appliances ORDER BY id DESC")
    rows = cur.fetchall()
    conn.close()
    result = []
    for r in rows:
        result.append({
            "id": r[0],
            "name": r[1],
            "energy_kwh": r[2],
            "price": r[3],
            "energy_rate": r[4],
            "created_at": r[5]
        })
    return jsonify(result)

@app.route("/api/compare", methods=["POST"])
def api_compare():
    data = request.get_json() or {}
    ids = data.get("ids") or data.get("id") or []
    if not isinstance(ids, list) or len(ids) != 2:
        return jsonify({"error": "Provide array 'ids' with two appliance IDs"}), 400
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, name, energy_kwh, price, energy_rate FROM appliances WHERE id IN (?,?)", (ids[0], ids[1]))
    rows = cur.fetchall()
    conn.close()
    if len(rows) != 2:
        return jsonify({"error": "Two valid appliances required"}), 404
    a = rows[0]
    b = rows[1]

    def metrics(row):
        _, name, energy_kwh, price, rate = row
        annual_kwh = float(energy_kwh) if energy_kwh else 0.0
        annual_cost = round(annual_kwh * (float(rate) if rate else 0.0), 2)
        monthly_cost = round(annual_cost / 12.0, 2)
        carbon = round(annual_kwh * CO2_FACTOR, 2)
        return {"name": name, "annual_kwh": annual_kwh, "annual_cost": annual_cost, "monthly_cost": monthly_cost, "carbon_kg": carbon, "price": price}

    ma = metrics(a)
    mb = metrics(b)
    recommended = ma["name"] if ma["annual_cost"] < mb["annual_cost"] else mb["name"]
    # time to recover higher price: if one is more expensive, (price_diff) / yearly_savings
    try:
        price_diff = abs((ma["price"] or 0) - (mb["price"] or 0))
        yearly_savings = abs(ma["annual_cost"] - mb["annual_cost"])
        time_months = math.inf if yearly_savings == 0 else round((price_diff / yearly_savings) * 12, 1)
        time_months = "∞" if time_months == math.inf else time_months
    except Exception:
        time_months = None

    return jsonify({"A": ma, "B": mb, "recommended": recommended, "time_to_save_months": time_months})

@app.route("/api/export_pdf", methods=["GET"])
def api_export_pdf():
    # create PDF summary of all appliances and comparisons
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT name, energy_kwh, price, energy_rate, created_at FROM appliances ORDER BY id")
    rows = cur.fetchall()
    conn.close()

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(40, 800, "WattCompare Report")
    pdf.setFont("Helvetica", 11)
    y = 780
    for r in rows:
        name, energy_kwh, price, energy_rate, created_at = r
        pdf.drawString(40, y, f"{name} — {energy_kwh} kWh/year | Price: {price} | Rate: {energy_rate}/kWh | Added: {created_at}")
        y -= 18
        if y < 80:
            pdf.showPage()
            y = 800

    pdf.save()
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name="WattCompare_Report.pdf", mimetype="application/pdf")

# Health endpoint
@app.route("/health")
def health():
    return jsonify({"status": "ok", "time": datetime.utcnow().isoformat()})

# Run the app
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print("Starting WattCompare on port", port)
    app.run(host="0.0.0.0", port=port)
