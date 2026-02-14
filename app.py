from flask import Flask, render_template, request, redirect, url_for, flash
from werkzeug.utils import secure_filename
import os
import sqlite3
import uuid
from datetime import datetime
import cv2
app = Flask(__name__)
app.secret_key = "change-this-secret"
UPLOAD_FOLDER = "static/uploads"
ALLOWED_EXT = {"png", "jpg", "jpeg", "webp"}
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
DB_PATH = "database.db"
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn
def init_db():
    conn = db()
    conn.execute("""
    CREATE TABLE IF NOT EXISTS reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        issue TEXT,
        location TEXT,
        latitude REAL,
        longitude REAL,
        severity TEXT,
        description TEXT,
        department TEXT,
        status TEXT,
        image TEXT,
        image_hash TEXT,
        created_at TEXT,
        updated_at TEXT
    )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_reports_issue ON reports(issue)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_reports_created_at ON reports(created_at)")
    conn.commit()
    conn.close()
def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT
def compute_image_hash(path: str) -> str:
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return ""
    img = cv2.resize(img, (64, 64))
    mean = img.mean()
    bits = (img > mean).astype("uint8")
    return "".join(bits.flatten().astype(str).tolist())
def predict_issue(image_path: str) -> str:
    img = cv2.imread(image_path)
    if img is None:
        return "Invalid Image"
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(gray, 50, 150)
    edge_count = cv2.countNonZero(edges)
    total = edges.shape[0] * edges.shape[1]
    edge_ratio = edge_count / max(total, 1)
    if edge_ratio > 0.085:
        return "Broken Road"
    elif edge_ratio > 0.055:
        return "Pothole"
    elif edge_ratio > 0.030:
        return "Garbage"
    else:
        return "Open Manhole"

def auto_department(issue: str) -> str:
    mapping = {
        "Pothole": "Roads Department",
        "Broken Road": "Roads Department",
        "Garbage": "Sanitation Department",
        "Open Manhole": "Water Works Department",
        "Invalid Image": "Review Team"
    }
    return mapping.get(issue, "Review Team")

def auto_severity(issue: str) -> str:
    if issue == "Open Manhole":
        return "High"
    if issue in ("Broken Road", "Pothole"):
        return "Medium"
    if issue == "Garbage":
        return "Low"
    return "Low"
init_db()
@app.route("/")
def home():
    return render_template("index.html")
@app.route("/predict", methods=["POST"])
def predict():
    file = request.files.get("file")
    location = request.form.get("location", "").strip()
    description = request.form.get("description", "").strip()
    lat = request.form.get("latitude", "").strip()
    lon = request.form.get("longitude", "").strip()
    latitude = float(lat) if lat else None
    longitude = float(lon) if lon else None
    if not file or file.filename == "":
        flash("Please upload an image.", "error")
        return redirect(url_for("home"))
    if not allowed_file(file.filename):
        flash("Unsupported file type. Use JPG/PNG/JPEG/WEBP.", "error")
        return redirect(url_for("home"))
    safe_name = secure_filename(file.filename)
    ext = safe_name.rsplit(".", 1)[1].lower()
    new_name = f"{uuid.uuid4().hex}.{ext}"
    filepath = os.path.join(app.config["UPLOAD_FOLDER"], new_name)
    file.save(filepath)
    issue = predict_issue(filepath)
    department = auto_department(issue)
    severity = auto_severity(issue)
    img_hash = compute_image_hash(filepath)
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    conn = db()
    dup = conn.execute(
        """
        SELECT id FROM reports
        WHERE image_hash = ? AND location = ?
        AND created_at >= datetime('now','-7 days')
        LIMIT 1
        """,
        (img_hash, location)
    ).fetchone()
    if dup:
        conn.close()
        flash(f"Duplicate report detected (similar image). Existing Report ID: {dup['id']}", "error")
        try:
            os.remove(filepath)
        except Exception:
            pass
        return redirect(url_for("dashboard"))
    conn.execute(
        """
        INSERT INTO reports (issue, location, latitude, longitude, severity, description,
                             department, status, image, image_hash, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (issue, location, latitude, longitude, severity, description,
         department, "New", new_name, img_hash, now, now)
    )
    conn.commit()
    conn.close()
    return render_template(
        "result.html",
        prediction=issue,
        location=location,
        severity=severity,
        description=description,
        department=department,
        status="New",
        image=new_name,
        latitude=latitude,
        longitude=longitude
    )
@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    q_issue = request.values.get("issue", "").strip()
    q_status = request.values.get("status", "").strip()
    q_severity = request.values.get("severity", "").strip()
    q_search = request.values.get("search", "").strip()
    where = []
    params = []
    if q_issue:
        where.append("issue = ?")
        params.append(q_issue)
    if q_status:
        where.append("status = ?")
        params.append(q_status)
    if q_severity:
        where.append("severity = ?")
        params.append(q_severity)
    if q_search:
        where.append("(location LIKE ? OR description LIKE ?)")
        params.extend([f"%{q_search}%", f"%{q_search}%"])
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"SELECT * FROM reports {where_sql} ORDER BY id DESC"
    conn = db()
    reports = conn.execute(sql, params).fetchall()
    all_reports = conn.execute("SELECT issue, status FROM reports").fetchall()
    conn.close()
    total = len(all_reports)
    potholes = sum(1 for r in all_reports if r["issue"] == "Pothole")
    garbage = sum(1 for r in all_reports if r["issue"] == "Garbage")
    manhole = sum(1 for r in all_reports if r["issue"] == "Open Manhole")
    road = sum(1 for r in all_reports if r["issue"] == "Broken Road")
    new_count = sum(1 for r in all_reports if r["status"] == "New")
    resolved_count = sum(1 for r in all_reports if r["status"] == "Resolved")
    return render_template(
        "dashboard.html",
        reports=reports,
        total=total,
        potholes=potholes,
        garbage=garbage,
        manhole=manhole,
        road=road,
        new_count=new_count,
        resolved_count=resolved_count,
        q_issue=q_issue,
        q_status=q_status,
        q_severity=q_severity,
        q_search=q_search
    )
@app.route("/update_status/<int:report_id>", methods=["POST"])
def update_status(report_id):
    new_status = request.form.get("status", "New")
    new_department = request.form.get("department", "Review Team")
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    conn = db()
    conn.execute(
        "UPDATE reports SET status = ?, department = ?, updated_at = ? WHERE id = ?",
        (new_status, new_department, now, report_id)
    )
    conn.commit()
    conn.close()
    return redirect(url_for("dashboard"))
if __name__ == "__main__":
    app.run
