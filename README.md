# Voucher Anexo 5 Sistema Red

Genera vouchers A4 en PDF a partir del **Consolidado Anexo-5**, que une los Anexo 5 de todas las unidades de negocio.

## Cómo funciona

- **Consolidado Anexo-5**: el administrador sube en `/admin` el Excel de cada unidad. Se lee la hoja `Horarios de pasada_completo`, se ignoran las filas 1 a 6 y los encabezados se toman de la fila 7. Las columnas se ubican por nombre de encabezado (no todas las unidades tienen las mismas letras: U2 trae "Código paradero TS" adicional). Subir de nuevo una unidad reemplaza sus datos. El consolidado se puede descargar como CSV.
- **Filtros**: Unidad (col. B), Ciclo (encabezado "Tipo de Medición": col. P en U16/U18/U19, col. Q en U2), Tipo Día (col. F), Servicio (col. D, selección múltiple) y Sentido (col. E, selección múltiple). No hay filtro de hora de salida.
- **PDF**: solo botón "Generar PDF". Voucher en tarjetas recortables de 2 columnas, una por expedición, con Unidad, Ciclo, Servicio, Sentido, Tipo día, Expedición, horario de salida desde cabezal y los paraderos con su horario de pasada. La altura de cada tarjeta se ajusta a la cantidad de paraderos.
- **Accesos**: cada unidad ingresa con su clave (usuario `u<N>`) y solo ve e imprime su unidad. Los administradores ven todas, cargan anexos, crean/bloquean accesos y cambian claves. Cada PDF generado queda registrado en `descargas`.

## Configuración (Render)

| Variable | Uso |
|---|---|
| `DATABASE_URL` | Cadena de conexión de Neon (Postgres) |
| `SECRET_KEY` | Firma de la sesión (Render la genera) |
| `ADMIN_USUARIO` / `ADMIN_CLAVE` | Administrador inicial, se crea al primer arranque si no existe ninguno |
| `ZONA_HORARIA` | Zona para la fecha impresa en el PDF (por defecto `America/Santiago`) |

Las tablas (`consolidado_anexo5`, `accesos`, `cargas`, `descargas`) se crean solas al arrancar.

## Ejecutar localmente

```bash
pip install -r requirements.txt
DATABASE_URL=postgresql://... ADMIN_CLAVE=algo-seguro gunicorn -w 1 --threads 4 app:app
```
