"""Lectura de los Anexo 5 por unidad de negocio y armado del Consolidado Anexo-5.

Las filas 1 a 6 son la cabecera del documento y se ignoran; los encabezados están en la fila 7.
Las columnas se ubican por nombre de encabezado y no por letra, porque no todas las unidades
traen las mismas columnas (por ejemplo U2 agrega "Código paradero TS" y corre el resto).
"""

import datetime as dt
import unicodedata

import openpyxl

FILA_ENCABEZADO = 7

# campo interno -> función que reconoce el encabezado normalizado
CAMPOS = {
    "unidad": lambda h: h in ("unidad de servicio", "unidad de negocio"),
    "servicio_ts": lambda h: h == "codigo ts servicio",
    "servicio": lambda h: h == "codigo usuario servicio",
    "sentido": lambda h: h == "sentido",
    "tipo_dia": lambda h: h == "tipo dia",
    "expedicion": lambda h: h == "expedicion",
    "paradero_cod": lambda h: h == "codigo paradero usuario",
    "paradero_nombre": lambda h: h == "nombre paradero",
    "correlativo": lambda h: h.startswith("correlativo"),
    "hora_pasada": lambda h: h.startswith("horario de pasada"),
    "hora_salida": lambda h: h.startswith("horario de salida"),
    "ciclo": lambda h: h == "tipo de medicion",
}
OBLIGATORIOS = ("unidad", "servicio", "sentido", "tipo_dia", "expedicion", "hora_pasada", "hora_salida", "ciclo")
COLUMNAS = ("unidad", "ciclo", "tipo_dia", "servicio", "servicio_ts", "sentido", "expedicion",
            "paradero_cod", "paradero_nombre", "correlativo", "hora_pasada", "hora_salida")


class AnexoError(ValueError):
    pass


def _norm(v):
    s = unicodedata.normalize("NFKD", str(v or "")).encode("ascii", "ignore").decode()
    return " ".join(s.lower().replace("\n", " ").split())


def _hora(v):
    if v is None or v == "":
        return None
    if isinstance(v, dt.datetime):
        v = v.time()
    if isinstance(v, dt.time):
        return f"{v.hour:02d}:{v.minute:02d}"
    if isinstance(v, (int, float)):  # fracción de día de Excel
        m = round((float(v) % 1) * 1440)
        return f"{m // 60 % 24:02d}:{m % 60:02d}"
    s = str(v).strip()
    p = s.split(":")
    if len(p) >= 2 and p[0].isdigit() and p[1][:2].isdigit():
        return f"{int(p[0]) % 24:02d}:{int(p[1][:2]):02d}"
    return s[:5]


def _texto(v):
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def _entero(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _mapa(encabezados):
    mapa = {}
    for i, h in enumerate(encabezados):
        n = _norm(h)
        for campo, ok in CAMPOS.items():
            if campo not in mapa and ok(n):
                mapa[campo] = i
    return mapa


def leer_anexo(archivo):
    """Devuelve (filas, info). filas: lista de tuplas en el orden de COLUMNAS."""
    wb = openpyxl.load_workbook(archivo, read_only=True, data_only=True)
    try:
        hoja, mapa = None, None
        # preferir la hoja "completo", que es la que trae el ciclo
        orden = sorted(wb.sheetnames, key=lambda n: 0 if "completo" in _norm(n) else 1)
        for nombre in orden:
            ws = wb[nombre]
            enc = next(ws.iter_rows(min_row=FILA_ENCABEZADO, max_row=FILA_ENCABEZADO, values_only=True), ())
            m = _mapa(enc)
            if all(c in m for c in OBLIGATORIOS):
                hoja, mapa = ws, m
                break
        if hoja is None:
            raise AnexoError("No encontré una hoja con encabezados en la fila 7 que incluya Ciclo (Tipo de Medición), "
                             "Servicio, Sentido, Tipo Día, Expedición y horarios.")
        info = {"hoja": hoja.title}
        for fila in hoja.iter_rows(min_row=1, max_row=6, values_only=True):
            etiqueta = _norm(fila[0]) if fila else ""
            valor = next((x for x in fila[1:] if x not in (None, "")), None)
            if etiqueta == "fecha inicio":
                info["fecha_inicio"] = valor.date() if isinstance(valor, dt.datetime) else None
            elif etiqueta == "fecha termino":
                info["fecha_termino"] = valor.date() if isinstance(valor, dt.datetime) else None

        filas = []
        for r in hoja.iter_rows(min_row=FILA_ENCABEZADO + 1, values_only=True):
            g = lambda c: r[mapa[c]] if c in mapa and mapa[c] < len(r) else None
            unidad = _entero(g("unidad"))
            if unidad is None:
                continue
            filas.append((
                unidad,
                _texto(g("ciclo")) or "Sin ciclo",
                _texto(g("tipo_dia")),
                _texto(g("servicio")),
                _texto(g("servicio_ts")),
                _texto(g("sentido")),
                _entero(g("expedicion")),
                _texto(g("paradero_cod")),
                _texto(g("paradero_nombre")),
                _entero(g("correlativo")),
                _hora(g("hora_pasada")),
                _hora(g("hora_salida")),
            ))
        if not filas:
            raise AnexoError("La hoja no tiene filas de datos bajo la fila 7.")
        unidades = sorted({f[0] for f in filas})
        info.update(filas=len(filas), unidades=unidades)
        return filas, info
    finally:
        wb.close()
