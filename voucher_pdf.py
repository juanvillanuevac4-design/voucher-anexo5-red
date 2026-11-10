"""Generación del PDF de vouchers en hoja A4.

Formatos:
  tarjetas3  tarjetas compactas en 3 columnas (propuesta 2, por defecto)
  tarjetas2  tarjetas con nombre de paradero en 2 columnas (propuesta 1)
  planilla   una tabla por servicio y sentido: fila = expedición, columna = paradero (propuesta 3)
La altura de cada tarjeta se ajusta a la cantidad de paraderos, así caben más por hoja.
"""

import io
from collections import OrderedDict

from reportlab.lib.colors import HexColor, white, black
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

ROJO = HexColor("#E30613")
GRIS = HexColor("#777777")
LINEA = HexColor("#BBBBBB")
W, H = A4
M = 22  # margen
PIE = 16
FORMATOS = {"tarjetas3": "Tarjetas compactas (3 columnas)",
            "tarjetas2": "Tarjetas con nombre de paradero (2 columnas)",
            "planilla": "Planilla por servicio y sentido"}


def agrupar(filas):
    """filas: dicts ordenados por servicio, sentido, hora_salida, expedicion, correlativo.
    Devuelve lista de expediciones: {servicio, sentido, expedicion, salida, paraderos:[(cod, nombre, hora)]}"""
    exps = OrderedDict()
    for f in filas:
        k = (f["servicio"], f["sentido"], f["expedicion"], f["hora_salida"])
        e = exps.get(k)
        if e is None:
            e = exps[k] = {"servicio": f["servicio"], "sentido": f["sentido"], "expedicion": f["expedicion"],
                           "salida": f["hora_salida"], "paraderos": []}
        e["paraderos"].append((f["paradero_cod"] or "", f["paradero_nombre"] or "", f["hora_pasada"] or ""))
    return list(exps.values())


def _corta(txt, fuente, tam, ancho):
    if stringWidth(txt, fuente, tam) <= ancho:
        return txt
    while txt and stringWidth(txt + "…", fuente, tam) > ancho:
        txt = txt[:-1]
    return txt + "…"


class _Doc:
    def __init__(self, titulo, generado):
        self.buf = io.BytesIO()
        self.c = canvas.Canvas(self.buf, pagesize=A4)
        self.c.setTitle(titulo)
        self.c.setAuthor("Voucher Anexo 5 Sistema Red")
        self.generado = generado
        self.pagina = 1

    def pie(self):
        c = self.c
        c.setStrokeColor(LINEA)
        c.setLineWidth(0.5)
        c.line(M, M + 8, W - M, M + 8)
        c.setFont("Helvetica", 6.5)
        c.setFillColor(GRIS)
        c.drawString(M, M, "Voucher Anexo 5 · Sistema Red · Consolidado Anexo-5")
        c.drawRightString(W - M, M, f"Generado {self.generado} · Hoja {self.pagina}")
        c.setFillColor(black)

    def nueva(self):
        self.pie()
        self.c.showPage()
        self.pagina += 1

    def cerrar(self):
        self.pie()
        self.c.save()
        return self.buf.getvalue()


def _tarjetas(doc, exps, meta, cols):
    c = doc.c
    compacta = cols == 3
    fs = 6.6 if compacta else 7.6       # tamaño de letra del cuerpo
    lh = fs + 2.4                       # alto de cada fila de paradero
    cab = 13 if compacta else 30        # encabezado de la tarjeta
    sub = 9 if compacta else 0
    th = lh
    pad = 5
    gap = 6
    ancho = (W - 2 * M - gap * (cols - 1)) / cols
    util_top = H - M
    util_bot = M + PIE

    def alto(e):
        return pad * 2 + cab + sub + th + lh * len(e["paraderos"])

    x_i, y = 0, util_top
    fila_alto = 0
    for e in exps:
        h = alto(e)
        if x_i == 0:
            if y - h < util_bot:
                doc.nueva()
                y = util_top
            fila_alto = 0
        fila_alto = max(fila_alto, h)
        x = M + x_i * (ancho + gap)
        _tarjeta(c, x, y, ancho, h, e, meta, compacta, fs, lh, cab, sub, pad)
        x_i += 1
        if x_i == cols:
            x_i = 0
            y -= fila_alto + gap
    return doc


def _tarjeta(c, x, y, w, h, e, meta, compacta, fs, lh, cab, sub, pad):
    # borde punteado = línea de corte
    c.setDash(2, 2)
    c.setStrokeColor(GRIS)
    c.setLineWidth(0.4)
    c.rect(x, y - h, w, h)
    c.setDash()
    ix, iw = x + pad, w - 2 * pad
    top = y - pad
    if compacta:
        c.setFillColor(ROJO)
        c.rect(ix, top - cab, iw, cab, stroke=0, fill=1)
        c.setFillColor(white)
        c.setFont("Helvetica-Bold", 7.2)
        c.drawString(ix + 3, top - cab + 4, _corta(f"{e['servicio']} · {e['sentido']} · Exp {e['expedicion']}",
                                                   "Helvetica-Bold", 7.2, iw - 60))
        c.drawRightString(ix + iw - 3, top - cab + 4, f"Salida {e['salida']}")
        c.setFillColor(black)
        c.setFont("Helvetica", 6.4)
        c.drawString(ix, top - cab - 7, _corta(f"U{meta['unidad']} · {meta['ciclo']} · {meta['tipo_dia']}",
                                              "Helvetica", 6.4, iw))
        y0 = top - cab - sub
        cols = [("Cód.", 0.22, "c"), ("Paradero", 0.58, "l"), ("Pasada", 0.20, "c")]
    else:
        caja = 62
        c.setStrokeColor(black)
        c.setLineWidth(0.6)
        c.rect(ix, top - cab, iw, cab)
        c.setFillColor(ROJO)
        c.rect(ix + iw - caja, top - cab, caja, cab, stroke=0, fill=1)
        c.setFillColor(white)
        c.setFont("Helvetica-Bold", 5.6)
        c.drawCentredString(ix + iw - caja / 2, top - 7, "SALIDA DESDE")
        c.drawCentredString(ix + iw - caja / 2, top - 13, "CABEZAL")
        c.rect(ix + iw - caja + 6, top - cab + 3, caja - 12, 11, stroke=0, fill=1)
        c.setFillColor(black)
        c.setFont("Helvetica-Bold", 9)
        c.drawCentredString(ix + iw - caja / 2, top - cab + 5.5, e["salida"] or "")
        c.setFont("Helvetica", 7)
        tw = iw - caja - 8
        c.drawString(ix + 4, top - 8.5, _corta(f"Unidad: {meta['unidad']}   Ciclo: {meta['ciclo']}", "Helvetica", 7, tw))
        c.drawString(ix + 4, top - 17, _corta(f"Servicio: {e['servicio']}   Sentido: {e['sentido']}", "Helvetica", 7, tw))
        c.drawString(ix + 4, top - 25.5, _corta(f"Tipo día: {meta['tipo_dia']}   Expedición: {e['expedicion']}",
                                               "Helvetica", 7, tw))
        y0 = top - cab - 3
        cols = [("Código paradero", 0.22, "c"), ("Nombre paradero", 0.56, "l"), ("Hora de pasada", 0.22, "c")]
    # encabezado de tabla
    c.setFillColor(ROJO)
    c.rect(ix, y0 - lh, iw, lh, stroke=0, fill=1)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", fs - 0.8)
    cx = ix
    for t, p, al in cols:
        cw = iw * p
        if al == "l":
            c.drawString(cx + 2, y0 - lh + 2.6, t)
        else:
            c.drawCentredString(cx + cw / 2, y0 - lh + 2.6, t)
        cx += cw
    c.setFillColor(black)
    yy = y0 - lh
    for cod, nom, hora in e["paraderos"]:
        yy -= lh
        cx = ix
        for (t, p, al), val, f in zip(cols, (cod, nom, hora), ("Helvetica", "Helvetica", "Helvetica-Bold")):
            cw = iw * p
            c.setFont(f, fs)
            if al == "l":
                c.drawString(cx + 2, yy + 2.6, _corta(val, f, fs, cw - 4))
            else:
                c.drawCentredString(cx + cw / 2, yy + 2.6, _corta(val, f, fs, cw - 2))
            cx += cw
        c.setStrokeColor(LINEA)
        c.setLineWidth(0.3)
        c.line(ix, yy, ix + iw, yy)


def _planilla(doc, exps, meta):
    c = doc.c
    grupos = OrderedDict()
    for e in exps:
        grupos.setdefault((e["servicio"], e["sentido"]), []).append(e)
    lh = 11
    y = H - M
    for (serv, sent), lista in grupos.items():
        # columnas de paraderos en orden de recorrido
        orden = OrderedDict()
        for e in lista:
            for cod, nom, _ in e["paraderos"]:
                orden.setdefault(cod, nom)
        pars = list(orden.items())
        fijo = 26 + 44
        pw = (W - 2 * M - fijo) / max(1, len(pars))
        head_h = 24
        th = 22

        def cabecera(y, cont=False):
            c.setStrokeColor(black)
            c.setLineWidth(0.6)
            c.rect(M, y - head_h, W - 2 * M, head_h)
            c.setFillColor(ROJO)
            c.rect(W - M - 70, y - head_h, 70, head_h, stroke=0, fill=1)
            c.setFillColor(white)
            c.setFont("Helvetica-Bold", 6)
            c.drawCentredString(W - M - 35, y - 8, "EXPEDICIONES")
            c.setFont("Helvetica-Bold", 10)
            c.drawCentredString(W - M - 35, y - 19, str(len(lista)))
            c.setFillColor(black)
            c.setFont("Helvetica", 8)
            c.drawString(M + 5, y - 10, f"Unidad: {meta['unidad']}    Ciclo: {meta['ciclo']}    Tipo día: {meta['tipo_dia']}")
            c.drawString(M + 5, y - 20, f"Servicio: {serv}    Sentido: {sent}" + ("   (continuación)" if cont else ""))
            y -= head_h + 4
            c.setFillColor(ROJO)
            c.rect(M, y - th, W - 2 * M, th, stroke=0, fill=1)
            c.setFillColor(white)
            c.setFont("Helvetica-Bold", 6.3)
            c.drawCentredString(M + 13, y - 13, "EXP.")
            c.drawCentredString(M + 26 + 22, y - 9, "SALIDA")
            c.drawCentredString(M + 26 + 22, y - 17, "CABEZAL")
            for i, (cod, nom) in enumerate(pars):
                cx = M + fijo + i * pw + pw / 2
                c.setFont("Helvetica-Bold", 6.3)
                c.drawCentredString(cx, y - 9, cod)
                c.setFont("Helvetica", 5.4)
                c.drawCentredString(cx, y - 17, _corta(nom, "Helvetica", 5.4, pw - 4))
            c.setFillColor(black)
            return y - th

        if y - (head_h + 4 + th + lh * 3) < M + PIE:
            doc.nueva()
            y = H - M
        y = cabecera(y)
        for e in lista:
            if y - lh < M + PIE:
                doc.nueva()
                y = cabecera(H - M, cont=True)
            y -= lh
            horas = {cod: h for cod, _, h in e["paraderos"]}
            c.setFont("Helvetica", 7)
            c.drawCentredString(M + 13, y + 3, str(e["expedicion"]))
            c.setFont("Helvetica-Bold", 7)
            c.drawCentredString(M + 48, y + 3, e["salida"] or "")
            c.setFont("Helvetica", 7)
            for i, (cod, _) in enumerate(pars):
                c.drawCentredString(M + fijo + i * pw + pw / 2, y + 3, horas.get(cod, "—"))
            c.setStrokeColor(LINEA)
            c.setLineWidth(0.3)
            c.line(M, y, W - M, y)
        y -= 14
    return doc


def generar(filas, meta, formato, generado):
    exps = agrupar(filas)
    doc = _Doc(f"Voucher Anexo 5 U{meta['unidad']} {meta['ciclo']} {meta['tipo_dia']}", generado)
    if formato == "planilla":
        _planilla(doc, exps, meta)
    else:
        _tarjetas(doc, exps, meta, 2 if formato == "tarjetas2" else 3)
    return doc.cerrar(), len(exps), doc.pagina
