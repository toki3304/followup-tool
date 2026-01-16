import os
import sqlite3
from datetime import date, datetime, timedelta
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, flash
from flask_login import (
    LoginManager, UserMixin, login_user, login_required, logout_user, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.path.join(os.path.dirname(__file__), "followup.db")
DATE_FMT = "%Y-%m-%d"

def parse_date(s):
    s = (s or "").strip()
    if not s:
        return None
    return datetime.strptime(s, DATE_FMT).date()

def fmt_date(d):
    return d.strftime(DATE_FMT) if d else ""

def today():
    return date.today()

def connect_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = connect_db()
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS deals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        lead_type TEXT NOT NULL DEFAULT 'seller',      -- seller/buyer/owner
        name TEXT NOT NULL,
        phone TEXT,
        email TEXT,
        source TEXT,
        area TEXT,
        asset_type TEXT,
        price_yen INTEGER NOT NULL DEFAULT 0,
        deadline TEXT,                                -- YYYY-MM-DD
        deal_stage TEXT NOT NULL DEFAULT 'new',
        status TEXT NOT NULL DEFAULT 'active',         -- active/inactive
        last_contact TEXT,
        next_contact TEXT,
        next_action TEXT,
        notes TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """)
    conn.commit()
    conn.close()

def seed_if_empty():
    conn = connect_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM deals;")
    count = cur.fetchone()[0]
    if count == 0:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.commit()
    conn.close()


app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev_secret_change_me")

login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.init_app(app)

APP_USERNAME = os.environ.get("APP_USERNAME", "admin")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "3305")
APP_PASSWORD_HASH = generate_password_hash(APP_PASSWORD)

class User(UserMixin):
    id = "me"

@login_manager.user_loader
def load_user(user_id):
    if user_id == "me":
        return User()
    return None

@app.before_request
def _bootstrap():
    init_db()
    seed_if_empty()

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username","admin")
        password = request.form.get("password","3304")
        if username == APP_USERNAME and check_password_hash(APP_PASSWORD_HASH, password):
            login_user(User())
            return redirect(url_for("dashboard"))
        flash("ログイン情報が違います。", "error")
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

def query_deals(where="", params=(), order=""):
    conn = connect_db()
    sql = "SELECT * FROM deals"
    if where:
        sql += " WHERE " + where
    if order:
        sql += " ORDER BY " + order
    cur = conn.execute(sql, params)
    rows = cur.fetchall()
    conn.close()
    return rows

def get_deal(deal_id: int):
    conn = connect_db()
    row = conn.execute("SELECT * FROM deals WHERE id=?;", (deal_id,)).fetchone()
    conn.close()
    return row

@app.route("/")
@login_required
def dashboard():
    # 今日の追客（next_contact <= today）
    t = today().strftime(DATE_FMT)
    due = query_deals(
        where="status='active' AND next_contact IS NOT NULL AND next_contact<>'' AND next_contact <= ?",
        params=(t,),
        order="next_contact ASC"
    )

    # 期限が近い（deadlineが7日以内）
    upcoming = query_deals(
        where="status='active' AND deadline IS NOT NULL AND deadline<>'' AND deadline <= ?",
        params=((today() + timedelta(days=7)).strftime(DATE_FMT),),
        order="deadline ASC"
    )

    due = [dict(r, days_idle=days_since(r["last_contact"])) for r in due]
    upcoming = [dict(r, days_idle=days_since(r["last_contact"])) for r in upcoming]

    return render_template("dashboard.html", due=due, upcoming=upcoming, today=t)

@app.route("/deals")
@login_required
def deals():
    lead_type = request.args.get("lead_type")  # seller/buyer/owner
    stage = request.args.get("stage")
    area = request.args.get("area")

    where = []
    params = []

    if lead_type:
        where.append("lead_type=?")
        params.append(lead_type)
    if stage:
        where.append("deal_stage=?")
        params.append(stage)
    if area:
        where.append("area LIKE ?")
        params.append(f"%{area}%")

    where_sql = " AND ".join(where)
    rows = query_deals(where=where_sql, params=tuple(params), order="updated_at DESC")
    rows = [dict(r, days_idle=days_since(r["last_contact"])) for r in rows]
    return render_template("deals.html", rows=rows, lead_type=lead_type, stage=stage, area=area)

@app.route("/deals/new", methods=["GET", "POST"])
@login_required
def deal_new():
    if request.method == "POST":
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        data = (
            request.form.get("lead_type","seller"),
            request.form.get("name","").strip(),
            request.form.get("phone","").strip(),
            request.form.get("email","").strip(),
            request.form.get("source","").strip(),
            request.form.get("area","").strip(),
            request.form.get("asset_type","").strip(),
            int(request.form.get("price_yen","0") or 0),
            request.form.get("deadline","").strip(),
            request.form.get("deal_stage","new").strip(),
            request.form.get("status","active").strip(),
            request.form.get("last_contact","").strip(),
            request.form.get("next_contact","").strip(),
            request.form.get("next_action","").strip(),
            request.form.get("notes","").strip(),
            now, now
        )
        if not data[1]:
            flash("名前は必須です。", "error")
            return redirect(url_for("deal_new"))
        conn = connect_db()
        conn.execute("""
        INSERT INTO deals
        (lead_type,name,phone,email,source,area,asset_type,price_yen,deadline,deal_stage,status,last_contact,next_contact,next_action,notes,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?);
        """, data)
        conn.commit()
        conn.close()
        flash("追加しました。", "ok")
        return redirect(url_for("deals"))
    return render_template("deal_form.html")

@app.route("/deals/<int:deal_id>/edit", methods=["GET", "POST"])
@login_required
def deal_edit(deal_id):
    row = get_deal(deal_id)
    if not row:
        flash("案件が見つかりません。", "error")
        return redirect(url_for("deals"))

    if request.method == "POST":
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = connect_db()
        conn.execute("""
        UPDATE deals SET
          lead_type=?, name=?, phone=?, email=?, source=?, area=?, asset_type=?,
          price_yen=?, deadline=?, deal_stage=?, status=?, last_contact=?, next_contact=?,
          next_action=?, notes=?, updated_at=?
        WHERE id=?;
        """, (
            request.form.get("lead_type","seller"),
            request.form.get("name","").strip(),
            request.form.get("phone","").strip(),
            request.form.get("email","").strip(),
            request.form.get("source","").strip(),
            request.form.get("area","").strip(),
            request.form.get("asset_type","").strip(),
            int(request.form.get("price_yen","0") or 0),
            request.form.get("deadline","").strip(),
            request.form.get("deal_stage","new").strip(),
            request.form.get("status","active").strip(),
            request.form.get("last_contact","").strip(),
            request.form.get("next_contact","").strip(),
            request.form.get("next_action","").strip(),
            request.form.get("notes","").strip(),
            now,
            deal_id
        ))
        conn.commit()
        conn.close()
        flash("更新しました。", "ok")
        return redirect(url_for("deals"))

    return render_template("deal_edit.html", row=row)

@app.route("/deals/<int:deal_id>/touch", methods=["POST"])
@login_required
def deal_touch(deal_id):
    # 「連絡した」ボタン用：last_contact=今日、next_contact=今日+N日
    next_days = int(request.form.get("next_days","7"))
    stage = request.form.get("deal_stage","").strip()
    next_action = request.form.get("next_action","").strip()
    note = request.form.get("note","").strip()

    row = get_deal(deal_id)
    if not row:
        flash("案件が見つかりません。", "error")
        return redirect(url_for("deals"))

    lc = today()
    nc = today() + timedelta(days=next_days)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    notes = (row["notes"] or "")
    if note:
        stamp = lc.strftime(DATE_FMT)
        add = f"[{stamp}] {note}"
        notes = (notes + " / " + add).strip(" /") if notes else add

    conn = connect_db()
    conn.execute("""
    UPDATE deals SET
      last_contact=?, next_contact=?, deal_stage=COALESCE(NULLIF(?,''),deal_stage),
      next_action=COALESCE(NULLIF(?,''),next_action),
      notes=?, updated_at=?
    WHERE id=?;
    """, (lc.strftime(DATE_FMT), nc.strftime(DATE_FMT), stage, next_action, notes, now, deal_id))
    conn.commit()
    conn.close()
    flash("追客を更新しました。", "ok")
    return redirect(url_for("dashboard"))

@app.route("/pipeline")
@login_required
def pipeline():
    lead_type = request.args.get("lead_type")
    if lead_type:
        rows = query_deals(where="status='active' AND lead_type=?", params=(lead_type,))
    else:
        rows = query_deals(where="status='active'")

    counts = {}
    for r in rows:
        st = r["deal_stage"]
        counts[st] = counts.get(st, 0) + 1
    items = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    return render_template("pipeline.html", items=items, lead_type=lead_type)


def days_since(d):
    """YYYY-MM-DD からの経過日数。None/空は大きな値を返す"""
    try:
        if not d:
            return 999
        y, m, day = map(int, d.split("-"))
        return (date.today() - date(y, m, day)).days
    except Exception:
        return 999

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)

