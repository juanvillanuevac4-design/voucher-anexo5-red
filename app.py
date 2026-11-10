"""Voucher Anexo 5 Sistema Red.

Consolida los Anexo 5 de cada unidad de negocio en la tabla consolidado_anexo5 ("Consolidado Anexo-5")
y genera vouchers A4 en PDF filtrando por Unidad, Ciclo, Tipo Día, Servicio y Sentido.
Cada unidad ingresa con su clave y solo ve su unidad; el administrador ve todo y administra accesos.
"""

import datetime as dt
import os
import secrets
import threading
import time
from functools import wraps
from zoneinfo import ZoneInfo

import psycopg
from flask import Flask, Response, jsonify, redirect, request, send_from_directory, session
from flask.json.provider import DefaultJSONProvider
from psycopg.rows import dict_row
from werkzeug.security import check_password_hash, generate_password_hash

import voucher_pdf
from anexos import COLUMNAS, AnexoError, leer_anexo

DATABASE_URL = os.environ.get("DATABASE_URL", "")
ZONA = ZoneInfo(os.environ.get("ZONA_HORARIA", "America/Santiago"))
FORMATO = "tarjetas2"  # voucher elegido: propuesta 1, tarjetas con nombre de paradero, 2 columnas
BASE = os.path.dirname(os.path.abspath(__file__))

class _Json(DefaultJSONProvider):
    @staticmethod
    def default(o):
        if isinstance(o, (dt.date, dt.datetime)):
            return o.isoformat()
        return DefaultJSONProvider.default(o)


app = Flask(__name__, static_folder=None)
app.json = _Json(app)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.environ.get("RENDER")),
    PERMANENT_SESSION_LIFETIME=dt.timedelta(hours=12),
    MAX_CONTENT_LENGTH=60 * 1024 * 1024,
)

ESQUEMA = """
CREATE TABLE IF NOT EXISTS consolidado_anexo5 (
    id bigserial PRIMARY KEY,
    unidad int NOT NULL,
    ciclo text NOT NULL,
    tipo_dia text NOT NULL,
    servicio text NOT NULL,
    servicio_ts text,
    sentido text NOT NULL,
    expedicion int,
    paradero_cod text,
    paradero_nombre text,
    correlativo int,
    hora_pasada text,
    hora_salida text
);
CREATE INDEX IF NOT EXISTS ix_consolidado_filtro
    ON consolidado_anexo5 (unidad, ciclo, tipo_dia, servicio, sentido);
CREATE TABLE IF NOT EXISTS accesos (
    id serial PRIMARY KEY,
    usuario text UNIQUE NOT NULL,
    nombre text NOT NULL,
    rol text NOT NULL CHECK (rol IN ('admin', 'unidad')),
    unidad int,
    clave_hash text NOT NULL,
    activo boolean NOT NULL DEFAULT true,
    creado timestamptz NOT NULL DEFAULT now(),
    ultimo_ingreso timestamptz
);
CREATE TABLE IF NOT EXISTS cargas (
    id serial PRIMARY KEY,
    unidad int NOT NULL,
    archivo text,
    hoja text,
    filas int,
    fecha_inicio date,
    fecha_termino date,
    cargado_por text,
    cargado timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS descargas (
    id bigserial PRIMARY KEY,
    usuario text,
    unidad int,
    ciclo text,
    tipo_dia text,
    servicios text,
    sentidos text,
    formato text,
    expediciones int,
    hojas int,
    fecha timestamptz NOT NULL DEFAULT now()
);
"""


def db():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True)


_iniciado = threading.Lock()
_listo = False


def iniciar():
    global _listo
    if _listo or not DATABASE_URL:
        return
    with _iniciado:
        if _listo:
            return
        with db() as cx:
            cx.execute(ESQUEMA)
            usuario = os.environ.get("ADMIN_USUARIO", "admin").strip().lower()
            clave = os.environ.get("ADMIN_CLAVE")
            hay = cx.execute("SELECT 1 FROM accesos WHERE rol='admin' AND activo LIMIT 1").fetchone()
            if not hay and clave:
                cx.execute(
                    "INSERT INTO accesos (usuario, nombre, rol, clave_hash) VALUES (%s, 'Administrador', 'admin', %s) "
                    "ON CONFLICT (usuario) DO UPDATE SET rol='admin', activo=true, clave_hash=EXCLUDED.clave_hash",
                    (usuario, generate_password_hash(clave)))
        _listo = True


@app.before_request
def _antes():
    if request.endpoint not in ("salud", "estatico"):
        iniciar()


# ---------------------------------------------------------------- sesión

def usuario_actual():
    u = session.get("u")
    if not u:
        return None
    # revalida contra la base cada minuto (permite desactivar accesos al instante)
    if session.get("verificado", 0) < time.time() - 60:
        with db() as cx:
            r = cx.execute("SELECT id, usuario, nombre, rol, unidad FROM accesos WHERE id=%s AND activo",
                           (u["id"],)).fetchone()
        if not r:
            session.clear()
            return None
        session["u"] = u = dict(r)
        session["verificado"] = time.time()
    return u


def requiere(rol=None):
    def deco(f):
        @wraps(f)
        def envuelta(*a, **k):
            u = usuario_actual()
            if not u:
                if request.path.startswith("/api/"):
                    return jsonify(error="Tu sesión expiró. Vuelve a ingresar."), 401
                return redirect("/login")
            if rol and u["rol"] != rol:
                return jsonify(error="Solo el administrador puede hacer esto."), 403
            return f(u, *a, **k)
        return envuelta
    return deco


_intentos = {}


@app.get("/login")
def login_page():
    if usuario_actual():
        return redirect("/")
    return send_from_directory(os.path.join(BASE, "static"), "login.html")


@app.get("/api/accesos-login")
def accesos_login():
    with db() as cx:
        rs = cx.execute("SELECT usuario, nombre FROM accesos WHERE activo ORDER BY rol='admin', unidad NULLS LAST, nombre").fetchall()
    return jsonify(rs)


@app.post("/api/login")
def login():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
    ahora = time.time()
    lista = [t for t in _intentos.get(ip, []) if t > ahora - 300]
    if len(lista) >= 10:
        return jsonify(error="Demasiados intentos. Espera 5 minutos."), 429
    d = request.get_json(silent=True) or {}
    usuario = str(d.get("usuario", "")).strip().lower()
    clave = str(d.get("clave", ""))
    with db() as cx:
        r = cx.execute("SELECT * FROM accesos WHERE usuario=%s AND activo", (usuario,)).fetchone()
        if not r or not check_password_hash(r["clave_hash"], clave):
            lista.append(ahora)
            _intentos[ip] = lista
            return jsonify(error="Usuario o clave incorrectos."), 401
        cx.execute("UPDATE accesos SET ultimo_ingreso=now() WHERE id=%s", (r["id"],))
    _intentos.pop(ip, None)
    session.clear()
    session.permanent = True
    session["u"] = {k: r[k] for k in ("id", "usuario", "nombre", "rol", "unidad")}
    session["verificado"] = time.time()
    return jsonify(ok=True)


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/me")
@requiere()
def me(u):
    return jsonify(u)


# ---------------------------------------------------------------- páginas

@app.get("/")
@requiere()
def inicio(u):
    return send_from_directory(os.path.join(BASE, "static"), "index.html")


@app.get("/admin")
@requiere()
def admin_page(u):
    if u["rol"] != "admin":
        return redirect("/")
    return send_from_directory(os.path.join(BASE, "static"), "admin.html")


@app.get("/static/<path:p>", endpoint="estatico")
def estatico(p):
    return send_from_directory(os.path.join(BASE, "static"), p, max_age=3600)


@app.get("/healthz", endpoint="salud")
def salud():
    return "ok"


# ---------------------------------------------------------------- filtros y PDF

def _lista(v):
    if isinstance(v, list):
        return [str(x) for x in v if str(x).strip()]
    return [x for x in str(v or "").split(",") if x.strip()]


def _parametros(u, d):
    unidad = d.get("unidad")
    if u["rol"] != "admin":
        unidad = u["unidad"]
    try:
        unidad = int(unidad) if unidad not in (None, "") else None
    except ValueError:
        unidad = None
    return {
        "unidad": unidad,
        "ciclo": (d.get("ciclo") or None),
        "tipo_dia": (d.get("tipo_dia") or None),
        "servicios": _lista(d.get("servicios")),
        "sentidos": _lista(d.get("sentidos")),
    }


ORDEN_TIPO = "CASE tipo_dia WHEN 'Laboral' THEN 1 WHEN 'Sábado' THEN 2 WHEN 'Domingo' THEN 3 ELSE 4 END"
ORDEN_SERV = "substring(servicio from '^[A-Za-z]*'), NULLIF(substring(servicio from '[0-9]+'), '')::int, servicio"


@app.get("/api/filtros")
@requiere()
def filtros(u):
    p = _parametros(u, request.args)
    out = {"unidad": p["unidad"]}
    with db() as cx:
        if u["rol"] == "admin":
            out["unidades"] = [r["unidad"] for r in cx.execute(
                "SELECT DISTINCT unidad FROM consolidado_anexo5 ORDER BY unidad").fetchall()]
        else:
            out["unidades"] = [u["unidad"]]
        if p["unidad"] is None:
            return jsonify(out | {"ciclos": [], "tipos": [], "servicios": [], "sentidos": []})
        out["ciclos"] = [r["ciclo"] for r in cx.execute(
            "SELECT ciclo, count(*) n FROM consolidado_anexo5 WHERE unidad=%s GROUP BY ciclo ORDER BY ciclo",
            (p["unidad"],)).fetchall()]
        w, a = "unidad=%s", [p["unidad"]]
        if p["ciclo"]:
            w += " AND ciclo=%s"
            a.append(p["ciclo"])
        out["tipos"] = [r["tipo_dia"] for r in cx.execute(
            f"SELECT tipo_dia FROM consolidado_anexo5 WHERE {w} GROUP BY tipo_dia ORDER BY {ORDEN_TIPO}", a).fetchall()]
        if p["tipo_dia"]:
            w += " AND tipo_dia=%s"
            a.append(p["tipo_dia"])
        out["servicios"] = [r["servicio"] for r in cx.execute(
            f"SELECT servicio FROM consolidado_anexo5 WHERE {w} GROUP BY servicio ORDER BY {ORDEN_SERV}", a).fetchall()]
        if p["servicios"]:
            w += " AND servicio = ANY(%s)"
            a.append(p["servicios"])
        out["sentidos"] = [r["sentido"] for r in cx.execute(
            f"SELECT sentido FROM consolidado_anexo5 WHERE {w} GROUP BY sentido ORDER BY sentido='Ret', sentido", a).fetchall()]
    return jsonify(out)


def _completo(p):
    faltan = [n for n, k in (("Unidad", "unidad"), ("Ciclo", "ciclo"), ("Tipo día", "tipo_dia"),
                             ("Servicio", "servicios"), ("Sentido", "sentidos")) if not p[k]]
    return faltan


def _where(p):
    return ("unidad=%s AND ciclo=%s AND tipo_dia=%s AND servicio = ANY(%s) AND sentido = ANY(%s)",
            [p["unidad"], p["ciclo"], p["tipo_dia"], p["servicios"], p["sentidos"]])


@app.get("/api/resumen")
@requiere()
def resumen(u):
    p = _parametros(u, request.args)
    if _completo(p):
        return jsonify(grupos=[], expediciones=0, paraderos=0)
    w, a = _where(p)
    with db() as cx:
        g = cx.execute(f"""
            SELECT servicio, sentido, count(DISTINCT (expedicion, hora_salida)) expediciones,
                   count(DISTINCT paradero_cod) paraderos, max(cnt) max_paraderos,
                   min(hora_salida) primera, max(hora_salida) ultima
            FROM (SELECT *, count(*) OVER (PARTITION BY servicio, sentido, expedicion, hora_salida) cnt
                  FROM consolidado_anexo5 WHERE {w}) t
            GROUP BY servicio, sentido ORDER BY {ORDEN_SERV}, sentido='Ret'""", a).fetchall()
    return jsonify(grupos=g, expediciones=sum(x["expediciones"] for x in g),
                   paraderos=sum(x["paraderos"] for x in g))


@app.post("/api/pdf")
@requiere()
def pdf(u):
    d = request.get_json(silent=True) or {}
    p = _parametros(u, d)
    faltan = _completo(p)
    if faltan:
        return jsonify(error="Falta elegir: " + ", ".join(faltan) + "."), 400
    formato = FORMATO
    w, a = _where(p)
    with db() as cx:
        filas = cx.execute(f"""
            SELECT servicio, sentido, expedicion, hora_salida, paradero_cod, paradero_nombre, hora_pasada
            FROM consolidado_anexo5 WHERE {w}
            ORDER BY {ORDEN_SERV}, sentido='Ret', sentido, hora_salida, expedicion, correlativo, hora_pasada""",
                           a).fetchall()
        if not filas:
            return jsonify(error="No hay expediciones con esos filtros."), 404
        meta = {"unidad": p["unidad"], "ciclo": p["ciclo"], "tipo_dia": p["tipo_dia"]}
        ahora = dt.datetime.now(ZONA)
        contenido, n, hojas = voucher_pdf.generar(filas, meta, formato, ahora.strftime("%d-%m-%Y %H:%M"))
        cx.execute("INSERT INTO descargas (usuario, unidad, ciclo, tipo_dia, servicios, sentidos, formato, expediciones, hojas) "
                   "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                   (u["usuario"], p["unidad"], p["ciclo"], p["tipo_dia"], ",".join(p["servicios"]),
                    ",".join(p["sentidos"]), formato, n, hojas))
    nombre = f"Voucher_Anexo5_U{p['unidad']}_{p['ciclo']}_{p['tipo_dia']}_{ahora:%Y%m%d_%H%M}.pdf".replace(" ", "_")
    return Response(contenido, mimetype="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{nombre}"',
                             "X-Expediciones": str(n), "X-Hojas": str(hojas)})


# ---------------------------------------------------------------- administración

@app.get("/api/admin/estado")
@requiere("admin")
def admin_estado(u):
    with db() as cx:
        unidades = cx.execute("""
            SELECT c.unidad, c.filas, c.expediciones, c.servicios, c.ciclos,
                   k.archivo, k.fecha_inicio, k.fecha_termino, k.cargado, k.cargado_por
            FROM (SELECT unidad, count(*) filas, count(DISTINCT (ciclo, tipo_dia, servicio, sentido, expedicion, hora_salida)) expediciones,
                         count(DISTINCT servicio) servicios, string_agg(DISTINCT ciclo, ', ') ciclos
                  FROM consolidado_anexo5 GROUP BY unidad) c
            LEFT JOIN LATERAL (SELECT * FROM cargas WHERE cargas.unidad=c.unidad ORDER BY cargado DESC LIMIT 1) k ON true
            ORDER BY c.unidad""").fetchall()
        accesos = cx.execute("""SELECT id, usuario, nombre, rol, unidad, activo, creado, ultimo_ingreso
                                FROM accesos ORDER BY rol='unidad', unidad NULLS FIRST, usuario""").fetchall()
        descargas = cx.execute("""SELECT usuario, unidad, ciclo, tipo_dia, servicios, sentidos, formato, expediciones, hojas, fecha
                                  FROM descargas ORDER BY fecha DESC LIMIT 30""").fetchall()
    return jsonify(unidades=unidades, accesos=accesos, descargas=descargas)


@app.post("/api/admin/cargar")
@requiere("admin")
def admin_cargar(u):
    archivos = request.files.getlist("archivos")
    if not archivos:
        return jsonify(error="Adjunta al menos un archivo Excel."), 400
    resultados = []
    for f in archivos:
        try:
            filas, info = leer_anexo(f.stream)
        except AnexoError as e:
            resultados.append({"archivo": f.filename, "error": str(e)})
            continue
        except Exception:
            resultados.append({"archivo": f.filename, "error": "No pude leer el archivo. ¿Es un Excel .xlsx válido?"})
            continue
        with db() as cx:
            with cx.transaction():
                cx.execute("DELETE FROM consolidado_anexo5 WHERE unidad = ANY(%s)", (info["unidades"],))
                with cx.cursor().copy(f"COPY consolidado_anexo5 ({', '.join(COLUMNAS)}) FROM STDIN") as cp:
                    for r in filas:
                        cp.write_row(r)
                for un in info["unidades"]:
                    cx.execute("INSERT INTO cargas (unidad, archivo, hoja, filas, fecha_inicio, fecha_termino, cargado_por) "
                               "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                               (un, f.filename, info["hoja"], sum(1 for r in filas if r[0] == un),
                                info.get("fecha_inicio"), info.get("fecha_termino"), u["usuario"]))
        resultados.append({"archivo": f.filename, "unidades": info["unidades"], "filas": info["filas"], "hoja": info["hoja"]})
    return jsonify(resultados=resultados)


@app.delete("/api/admin/unidad/<int:unidad>")
@requiere("admin")
def admin_borrar_unidad(u, unidad):
    with db() as cx:
        n = cx.execute("DELETE FROM consolidado_anexo5 WHERE unidad=%s", (unidad,)).rowcount
    return jsonify(ok=True, filas=n)


@app.post("/api/admin/accesos")
@requiere("admin")
def admin_crear_acceso(u):
    d = request.get_json(silent=True) or {}
    rol = "admin" if d.get("rol") == "admin" else "unidad"
    clave = str(d.get("clave", ""))
    if len(clave) < 6:
        return jsonify(error="La clave debe tener al menos 6 caracteres."), 400
    if rol == "unidad":
        try:
            unidad = int(d.get("unidad"))
        except (TypeError, ValueError):
            return jsonify(error="Indica el número de unidad."), 400
        usuario = str(d.get("usuario") or f"u{unidad}").strip().lower()
        nombre = str(d.get("nombre") or f"Unidad {unidad}").strip()
    else:
        unidad = None
        usuario = str(d.get("usuario", "")).strip().lower()
        nombre = str(d.get("nombre") or usuario).strip()
        if not usuario:
            return jsonify(error="Indica el usuario del administrador."), 400
    with db() as cx:
        try:
            cx.execute("INSERT INTO accesos (usuario, nombre, rol, unidad, clave_hash) VALUES (%s,%s,%s,%s,%s)",
                       (usuario, nombre, rol, unidad, generate_password_hash(clave)))
        except psycopg.errors.UniqueViolation:
            return jsonify(error=f"El usuario {usuario} ya existe. Cambia su clave desde la lista."), 409
    return jsonify(ok=True)


@app.post("/api/admin/accesos/<int:id_>")
@requiere("admin")
def admin_editar_acceso(u, id_):
    d = request.get_json(silent=True) or {}
    with db() as cx:
        r = cx.execute("SELECT * FROM accesos WHERE id=%s", (id_,)).fetchone()
        if not r:
            return jsonify(error="Ese acceso ya no existe."), 404
        if "clave" in d:
            if len(str(d["clave"])) < 6:
                return jsonify(error="La clave debe tener al menos 6 caracteres."), 400
            cx.execute("UPDATE accesos SET clave_hash=%s WHERE id=%s", (generate_password_hash(str(d["clave"])), id_))
        if "activo" in d:
            if r["id"] == u["id"] and not d["activo"]:
                return jsonify(error="No puedes desactivar tu propio acceso."), 400
            cx.execute("UPDATE accesos SET activo=%s WHERE id=%s", (bool(d["activo"]), id_))
        if "nombre" in d and str(d["nombre"]).strip():
            cx.execute("UPDATE accesos SET nombre=%s WHERE id=%s", (str(d["nombre"]).strip(), id_))
    return jsonify(ok=True)


@app.delete("/api/admin/accesos/<int:id_>")
@requiere("admin")
def admin_borrar_acceso(u, id_):
    if id_ == u["id"]:
        return jsonify(error="No puedes eliminar tu propio acceso."), 400
    with db() as cx:
        cx.execute("DELETE FROM accesos WHERE id=%s", (id_,))
    return jsonify(ok=True)


@app.get("/api/admin/consolidado.csv")
@requiere("admin")
def admin_csv(u):
    def filas():
        yield "﻿" + ";".join(c.upper() for c in COLUMNAS) + "\n"
        with db() as cx, cx.transaction():
            with cx.cursor(name="exp") as cur:
                cur.execute(f"SELECT {', '.join(COLUMNAS)} FROM consolidado_anexo5 "
                            "ORDER BY unidad, ciclo, tipo_dia, servicio, sentido, hora_salida, expedicion, correlativo")
                for r in cur:
                    yield ";".join("" if r[c] is None else str(r[c]).replace(";", ",") for c in COLUMNAS) + "\n"
    return Response(filas(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="Consolidado_Anexo-5.csv"'})


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
