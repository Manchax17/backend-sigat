"""Acceso a datos de configuración. Fuentes: v_lote_app, v_trampa_app, v_trampa_lote_vigente,
fn_lote_app(usua), fn_trampa_app(usua), fn_lote_que_contiene(punto, instante)."""

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from sigat_api.compartido.errores import NoImplementado

# Las vistas de la aplicación (``v_lote_app``, ``v_trampa_app``) traen ya los nombres de campo
# que el móvil lee. Lo que no traen y el esquema exige se resuelve con estos joins:
#   * ``atrayente``          -> sigat.atrayente (el tipo de cebo no está en la vista)
#   * ``ubic_id``            -> v_trampa_lote_vigente (la ubicación vigente de la trampa)
#   * ``dentro_del_lote``    -> v_trampa_lote_vigente (comprobación espacial, RF-012)
_SELECT_TRAMPAS = """
SELECT v.*,
       a.atra_descripcion AS atrayente,
       vl.ubic_id,
       vl.dentro_del_lote,
       vl.lote_sin_geometria
  FROM {origen} v
  JOIN sigat.trampa t ON t.tram_id = v.tram_id
  LEFT JOIN sigat.atrayente a ON a.atra_id = t.atra_id
  LEFT JOIN sigat.v_trampa_lote_vigente vl ON vl.tram_id = v.tram_id
"""

_COLUMNAS_EDITABLES_LOTE = frozenset(
    {
        "lote_descripcion",
        "lote_area",
        "espc_id",
        "vari_id",
        "lote_fecha_plantacion",
        "lote_fecha_baja",
        "lote_fecha_actualizacion",
    }
)


class RepositorioConfiguracion:
    def __init__(self, con: AsyncConnection) -> None:
        self._con = con

    # predios
    async def predio_visible(self, usua_id: int, pred_id: int) -> bool:
        """Determina si un usuario tiene acceso a un predio según su rol (RNF-015, RF-002)."""
        sql = """
        SELECT EXISTS (
            SELECT 1 FROM sigat.usuario u
            JOIN sigat.rol r ON r.rol_id = u.rol_id
            WHERE u.usua_id = :u AND r.rol_codigo = 'ADMINISTRADOR'
            UNION ALL
            SELECT 1 FROM sigat.v_acceso_predio WHERE usua_id = :u AND pred_id = :pred_id
            UNION ALL
            SELECT 1 FROM sigat.fn_lotes_visibles(:u) lv
            JOIN sigat.lote l ON l.lote_id = lv.lote_id
            WHERE l.pred_id = :pred_id
        )
        """
        return bool(await self._con.scalar(text(sql), {"u": usua_id, "pred_id": pred_id}))

    async def predios_visibles(self, usua_id: int) -> list[dict[str, Any]]:
        sql = """
        SELECT p.pred_id,
               p.pred_codigo,
               p.pred_nombre,
               p.muni_id,
               m.muni_descripcion AS municipio,
               d.depa_descripcion AS departamento,
               p.pred_area,
               pe.pers_nombre || ' ' || pe.pers_apellido AS productor,
               p.pred_zona_horaria,
               (SELECT count(*)::int FROM sigat.lote l WHERE l.pred_id = p.pred_id AND l.lote_fecha_baja IS NULL) AS total_lotes,
               (p.pred_geom IS NOT NULL) AS tiene_geometria,
               (SELECT tec_pe.pers_nombre || ' ' || tec_pe.pers_apellido
                  FROM sigat.tecnico t
                  JOIN sigat.usuario tec_u ON tec_u.usua_id = t.usua_id
                  JOIN sigat.persona tec_pe ON tec_pe.pers_cc = tec_u.pers_cc
                 WHERE t.tecn_id = p.pred_tecnico_registro_id) AS responsable_tecnico
          FROM sigat.predio p
          JOIN sigat.municipio m ON m.muni_id = p.muni_id
          JOIN sigat.departamento d ON d.depa_id = m.depa_id
          JOIN sigat.persona pe ON pe.pers_cc = p.pers_cc
         WHERE (
            EXISTS (SELECT 1 FROM sigat.usuario u JOIN sigat.rol r ON r.rol_id = u.rol_id WHERE u.usua_id = :u AND r.rol_codigo = 'ADMINISTRADOR')
            OR p.pred_id IN (SELECT pred_id FROM sigat.v_acceso_predio WHERE usua_id = :u)
            OR p.pred_id IN (SELECT l.pred_id FROM sigat.fn_lotes_visibles(:u) lv JOIN sigat.lote l ON l.lote_id = lv.lote_id)
         )
         ORDER BY p.pred_nombre
        """
        filas = await self._con.execute(text(sql), {"u": usua_id})
        return [dict(f) for f in filas.mappings()]

    async def predio(self, pred_id: int) -> dict[str, Any] | None:
        sql = """
        SELECT p.pred_id,
               p.pred_codigo,
               p.pred_nombre,
               p.muni_id,
               m.muni_descripcion AS municipio,
               d.depa_descripcion AS departamento,
               p.pred_area,
               pe.pers_nombre || ' ' || pe.pers_apellido AS productor,
               p.pred_zona_horaria,
               (SELECT count(*)::int FROM sigat.lote l WHERE l.pred_id = p.pred_id AND l.lote_fecha_baja IS NULL) AS total_lotes,
               (p.pred_geom IS NOT NULL) AS tiene_geometria,
               (SELECT tec_pe.pers_nombre || ' ' || tec_pe.pers_apellido
                  FROM sigat.tecnico t
                  JOIN sigat.usuario tec_u ON tec_u.usua_id = t.usua_id
                  JOIN sigat.persona tec_pe ON tec_pe.pers_cc = tec_u.pers_cc
                 WHERE t.tecn_id = p.pred_tecnico_registro_id) AS responsable_tecnico
          FROM sigat.predio p
          JOIN sigat.municipio m ON m.muni_id = p.muni_id
          JOIN sigat.departamento d ON d.depa_id = m.depa_id
          JOIN sigat.persona pe ON pe.pers_cc = p.pers_cc
         WHERE p.pred_id = :pred_id
        """
        fila = (await self._con.execute(text(sql), {"pred_id": pred_id})).mappings().first()
        return dict(fila) if fila else None

    async def actualizar_predio(self, pred_id: int, cambios: dict[str, Any]) -> None:
        """Fija pred_fecha_actualizacion y pred_tecnico_actualizacion_id según la BD."""
        raise NoImplementado("Actualizar predio")

    async def mapa_predio(self, pred_id: int, instante: datetime) -> dict[str, Any]:
        """ST_AsGeoJSON de predio, lote_geometria vigente en ``instante``, reticula_celda y ubicacion_trampa."""
        # 1. Geometría del predio
        sql_pred = """
        SELECT pred_id, pred_nombre, pred_codigo,
               CASE WHEN pred_geom IS NULL THEN NULL ELSE ST_AsGeoJSON(pred_geom) END AS geom_json
          FROM sigat.predio
         WHERE pred_id = :pred_id
        """
        fila_pred = (await self._con.execute(text(sql_pred), {"pred_id": pred_id})).mappings().first()
        pred_geom = json.loads(fila_pred["geom_json"]) if fila_pred and fila_pred["geom_json"] else None
        pred_feature = {
            "type": "Feature",
            "id": pred_id,
            "geometry": pred_geom,
            "properties": {
                "pred_id": pred_id,
                "pred_nombre": fila_pred["pred_nombre"] if fila_pred else "",
                "pred_codigo": fila_pred["pred_codigo"] if fila_pred else None,
            },
        }

        # 2. Geometría de los lotes
        sql_lotes = """
        SELECT l.lote_id, l.lote_codigo, l.lote_descripcion, l.lote_area,
               CASE WHEN g.lotg_geom IS NULL THEN NULL ELSE ST_AsGeoJSON(g.lotg_geom) END AS geom_json
          FROM sigat.lote l
          LEFT JOIN LATERAL (
              SELECT lotg_geom
                FROM sigat.lote_geometria
               WHERE lote_id = l.lote_id
                 AND lotg_vigente_desde <= :instante
                 AND (lotg_vigente_hasta IS NULL OR lotg_vigente_hasta > :instante)
               ORDER BY lotg_version DESC
               LIMIT 1
          ) g ON true
         WHERE l.pred_id = :pred_id AND l.lote_fecha_baja IS NULL
         ORDER BY l.lote_codigo
        """
        filas_lotes = await self._con.execute(text(sql_lotes), {"pred_id": pred_id, "instante": instante})
        lotes_features: list[dict[str, Any]] = []
        for fl in filas_lotes.mappings():
            lg = json.loads(fl["geom_json"]) if fl["geom_json"] else None
            lotes_features.append(
                {
                    "type": "Feature",
                    "id": fl["lote_id"],
                    "geometry": lg,
                    "properties": {
                        "lote_id": fl["lote_id"],
                        "lote_codigo": fl["lote_codigo"],
                        "lote_descripcion": fl["lote_descripcion"],
                        "lote_area": float(fl["lote_area"]) if fl["lote_area"] is not None else None,
                    },
                }
            )

        # 3. Celdas de retícula
        sql_celdas = """
        SELECT rc.retc_id, rc.reti_id, rc.retc_codigo,
               ST_AsGeoJSON(rc.retc_geom) AS geom_json
          FROM sigat.reticula_celda rc
          JOIN sigat.reticula r ON r.reti_id = rc.reti_id
         WHERE r.pred_id = :pred_id
         ORDER BY rc.retc_codigo
        """
        filas_celdas = await self._con.execute(text(sql_celdas), {"pred_id": pred_id})
        celdas_features: list[dict[str, Any]] = []
        for fc in filas_celdas.mappings():
            cg = json.loads(fc["geom_json"]) if fc["geom_json"] else None
            celdas_features.append(
                {
                    "type": "Feature",
                    "id": fc["retc_id"],
                    "geometry": cg,
                    "properties": {
                        "retc_id": fc["retc_id"],
                        "reti_id": fc["reti_id"],
                        "retc_codigo": fc["retc_codigo"],
                    },
                }
            )

        # 4. Ubicación de trampas vigentes
        sql_trampas = """
        SELECT t.tram_id, t.tram_codigo, u.ubic_id, u.lote_id,
               ST_AsGeoJSON(u.ubic_geom) AS geom_json
          FROM sigat.ubicacion_trampa u
          JOIN sigat.trampa t ON t.tram_id = u.tram_id
         WHERE u.pred_id = :pred_id AND u.ubic_fecha_fin IS NULL
         ORDER BY t.tram_codigo
        """
        filas_trampas = await self._con.execute(text(sql_trampas), {"pred_id": pred_id})
        trampas_features: list[dict[str, Any]] = []
        for ft in filas_trampas.mappings():
            tg = json.loads(ft["geom_json"]) if ft["geom_json"] else None
            trampas_features.append(
                {
                    "type": "Feature",
                    "id": ft["tram_id"],
                    "geometry": tg,
                    "properties": {
                        "tram_id": ft["tram_id"],
                        "tram_codigo": ft["tram_codigo"],
                        "lote_id": ft["lote_id"],
                        "ubic_id": ft["ubic_id"],
                    },
                }
            )

        return {
            "predio": pred_feature,
            "lotes": {"type": "FeatureCollection", "features": lotes_features},
            "celdas": {"type": "FeatureCollection", "features": celdas_features},
            "trampas": {"type": "FeatureCollection", "features": trampas_features},
        }

    async def limites_predio(self, pred_id: int) -> list[float] | None:
        """Bounding box [minx, miny, maxx, maxy] EPSG:4326 del predio."""
        sql = """
        SELECT ST_XMin(pred_geom) AS minx,
               ST_YMin(pred_geom) AS miny,
               ST_XMax(pred_geom) AS maxx,
               ST_YMax(pred_geom) AS maxy
          FROM sigat.predio
         WHERE pred_id = :pred_id AND pred_geom IS NOT NULL
        """
        fila = (await self._con.execute(text(sql), {"pred_id": pred_id})).mappings().first()
        if not fila or fila["minx"] is None:
            return None
        return [float(fila["minx"]), float(fila["miny"]), float(fila["maxx"]), float(fila["maxy"])]

    # lotes
    async def lotes_visibles(self, usua_id: int, pred_id: int | None) -> list[dict[str, Any]]:
        """``SELECT * FROM fn_lote_app(:usua)`` (ya filtrado por fn_lotes_visibles).

        ``fn_lote_app`` ya aplica el permiso: el admin ve todos los lotes, el técnico solo los de
        sus actividades vigentes y el productor/agronomo los de sus predios. Aquí solo se acota por
        ``pred_id`` cuando el cliente lo pide.
        """
        filas = await self._con.execute(
            text(
                """
SELECT * FROM sigat.fn_lote_app(:usua)
 WHERE CAST(:pred_id AS integer) IS NULL OR pred_id = CAST(:pred_id AS integer)
 ORDER BY lote_id
"""
            ),
            {"usua": usua_id, "pred_id": pred_id},
        )
        return [dict(f) for f in filas.mappings()]

    async def lote(self, lote_id: int) -> dict[str, Any] | None:
        """Detalle de un lote: ``v_lote_app`` + las columnas de ``sigat.lote`` que solo pide el
        detalle (fechas, cultivo, variedad) + la versión vigente de la geometría (GeoJSON).

        La geometría vigente se toma con ``LATERAL``: la vista ``v_lote_app`` no la trae y hace
        falta un subplan que elija la versión más reciente que esté en vigor ahora.
        """
        fila = (
            (
                await self._con.execute(
                    text(
                        """
SELECT v.*,
       l.espc_id,
       l.vari_id,
       l.lote_fecha_plantacion,
       l.lote_fecha_registro,
       l.lote_fecha_baja,
       g.lotg_version AS geometria_version,
       CASE WHEN g.lotg_geom IS NULL THEN NULL ELSE ST_AsGeoJSON(g.lotg_geom) END AS geometria
  FROM sigat.v_lote_app v
  JOIN sigat.lote l ON l.lote_id = v.lote_id
  LEFT JOIN LATERAL (
       SELECT lotg_version, lotg_geom
         FROM sigat.lote_geometria
        WHERE lote_id = v.lote_id
          AND lotg_vigente_desde <= now()
          AND (lotg_vigente_hasta IS NULL OR lotg_vigente_hasta > now())
        ORDER BY lotg_version DESC
        LIMIT 1
  ) g ON true
 WHERE v.lote_id = :lote_id
"""
                    ),
                    {"lote_id": lote_id},
                )
            )
            .mappings()
            .first()
        )
        if fila is None:
            return None
        datos = dict(fila)
        if isinstance(datos.get("geometria"), str):
            datos["geometria"] = json.loads(datos["geometria"])
        return datos

    async def lote_visible(self, usua_id: int, lote_id: int) -> bool:
        """``lote_id IN (SELECT * FROM fn_lotes_visibles(:usua))``. Si no -> 404 (no 403, para no
        revelar que el lote existe pero es de otro usuario."""
        return bool(
            await self._con.scalar(
                text("SELECT EXISTS (SELECT 1 FROM sigat.fn_lotes_visibles(:usua) WHERE lote_id = :lote_id)"),
                {"usua": usua_id, "lote_id": lote_id},
            )
        )

    async def lote_predio(self, lote_id: int) -> int | None:
        valor = await self._con.scalar(
            text("SELECT pred_id FROM sigat.lote WHERE lote_id = :lote"), {"lote": lote_id}
        )
        return int(valor) if valor is not None else None

    async def pertenencia_trampa(self, tram_id: int, instante: datetime) -> dict[str, Any] | None:
        fila = (
            (
                await self._con.execute(
                    text(
                        """
SELECT u.ubic_id,
       u.lote_id,
       u.pred_id,
       t.tram_codigo,
       l.lote_fecha_registro,
       l.lote_fecha_baja
  FROM sigat.ubicacion_trampa u
  JOIN sigat.trampa t ON t.tram_id = u.tram_id
  JOIN sigat.lote l ON l.lote_id = u.lote_id
  JOIN sigat.predio p ON p.pred_id = u.pred_id
 WHERE u.tram_id = :tram
   AND u.ubic_fecha <= (CAST(:instante AS timestamptz) AT TIME ZONE p.pred_zona_horaria)::date
   AND (u.ubic_fecha_fin IS NULL
        OR u.ubic_fecha_fin > (CAST(:instante AS timestamptz) AT TIME ZONE p.pred_zona_horaria)::date)
 ORDER BY u.ubic_fecha DESC, u.ubic_id DESC
 LIMIT 1
"""
                    ),
                    {"tram": tram_id, "instante": instante},
                )
            )
            .mappings()
            .first()
        )
        return dict(fila) if fila else None

    async def lote_activo_en(self, lote_id: int, instante: date | datetime) -> bool:
        return bool(
            await self._con.scalar(
                text(
                    """
SELECT coalesce(
         l.lote_fecha_registro <= (CAST(:instante AS timestamptz) AT TIME ZONE p.pred_zona_horaria)::date
         AND (l.lote_fecha_baja IS NULL
              OR l.lote_fecha_baja > (CAST(:instante AS timestamptz) AT TIME ZONE p.pred_zona_horaria)::date),
         false)
  FROM sigat.lote l
  JOIN sigat.predio p ON p.pred_id = l.pred_id
 WHERE l.lote_id = :lote
"""
                ),
                {"lote": lote_id, "instante": instante},
            )
        )

    async def insertar_lote(self, pred_id: int, datos: dict[str, Any]) -> int:
        raise NoImplementado("Alta de lote")

    async def actualizar_lote(self, lote_id: int, cambios: dict[str, Any]) -> None:
        """UPDATE con lista blanca de columnas. ``sigat.usuario_actual`` ya lo fija ``sesion.transaccion()``."""
        if not cambios:
            return
        no_permitidas = set(cambios) - _COLUMNAS_EDITABLES_LOTE
        if no_permitidas:
            raise ValueError(f"Columnas no editables en lote: {sorted(no_permitidas)}")

        asignaciones = ", ".join(f"{k} = :{k}" for k in cambios)  # claves validadas contra la lista blanca
        sql = f"UPDATE sigat.lote SET {asignaciones} WHERE lote_id = :lote_id"  # noqa: S608
        await self._con.execute(text(sql), {**cambios, "lote_id": lote_id})

    async def nueva_version_geometria(
        self, lote_id: int, geojson: str, desde: datetime, fuente: str, motivo: str
    ) -> int:
        """En una transacción: cierra la vigente (lotg_vigente_hasta = desde) e inserta la nueva.
        La BD valida geometría (fn_lote_geometria_valida) y no solape (EXCLUDE)."""
        raise NoImplementado("Nueva versión de geometría")

    # trampas
    async def trampas_visibles(
        self, usua_id: int, lote_id: int | None, pred_id: int | None
    ) -> list[dict[str, Any]]:
        """``fn_trampa_app(:usua)`` + v_trampa_lote_vigente.

        ``fn_trampa_app`` ya filtra por permiso: solo salen las trampas de los lotes que el actor
        puede ver (``fn_lotes_visibles``). ``lote_id`` y ``pred_id`` solo acotan el resultado.
        """
        filas = await self._con.execute(
            text(
                _SELECT_TRAMPAS.format(origen="sigat.fn_trampa_app(:usua)")
                + """
 WHERE (CAST(:lote_id AS integer) IS NULL OR v.lote_id = CAST(:lote_id AS integer))
   AND (CAST(:pred_id AS integer) IS NULL OR v.pred_id = CAST(:pred_id AS integer))
 ORDER BY v.tram_codigo
"""
            ),
            {"usua": usua_id, "lote_id": lote_id, "pred_id": pred_id},
        )
        return [dict(f) for f in filas.mappings()]

    async def trampa_por_id(self, usua_id: int, tram_id: int) -> dict[str, Any] | None:
        """Trampa por identificador, dentro de las visibles para el actor."""
        fila = (
            (
                await self._con.execute(
                    text(
                        _SELECT_TRAMPAS.format(origen="sigat.fn_trampa_app(:usua)")
                        + " WHERE v.tram_id = :tram_id"
                    ),
                    {"usua": usua_id, "tram_id": tram_id},
                )
            )
            .mappings()
            .first()
        )
        return dict(fila) if fila is not None else None

    async def trampa_por_codigo(self, tram_codigo: str) -> dict[str, Any] | None:
        """Busca por código ICA en TODO el catálogo, no solo en los lotes visibles del actor.

        RF-012 compara trampa–lote–ubicación y AVISA, no bloquea: el técnico tiene que poder
        validar la trampa que tiene delante aunque no sea la que tiene asignada. Si se filtrara
        por visibilidad, la pantalla de validación no podría mostrar nada.

        ``con_ubicacion_vigente`` distingue «el código existe» de «la trampa tiene ubicación
        vigente» (sin ubicación no hay coordenadas que comparar).
        """
        fila = (
            (
                await self._con.execute(
                    text(
                        """
SELECT t.tram_id,
       t.tram_codigo,
       v.tipo,
       v.lote_id,
       v.pred_id,
       v.longitud,
       v.latitud,
       v.celda,
       v.estado,
       v.ultima_lectura,
       v.ultima_lectura_especimenes,
       a.atra_descripcion AS atrayente,
       vl.ubic_id,
       vl.dentro_del_lote,
       vl.lote_sin_geometria,
       (v.tram_id IS NOT NULL) AS con_ubicacion_vigente
  FROM sigat.trampa t
  LEFT JOIN sigat.v_trampa_app v ON v.tram_id = t.tram_id
  LEFT JOIN sigat.atrayente a ON a.atra_id = t.atra_id
  LEFT JOIN sigat.v_trampa_lote_vigente vl ON vl.tram_id = t.tram_id
 WHERE t.tram_codigo = :codigo
"""
                    ),
                    {"codigo": tram_codigo},
                )
            )
            .mappings()
            .first()
        )
        return dict(fila) if fila is not None else None

    async def lote_que_contiene(self, lon: float, lat: float, instante: datetime) -> int | None:
        """``fn_lote_que_contiene(ST_SetSRID(ST_MakePoint(lon, lat), 4326), instante)``."""
        valor = await self._con.scalar(
            text("SELECT sigat.fn_lote_que_contiene(ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), :instante)"),
            {"lon": lon, "lat": lat, "instante": instante},
        )
        return int(valor) if valor is not None else None

    async def tipo_trampa(self, tipt_id: int) -> dict[str, Any] | None:
        """Tipo de trampa con la paridad que exige al número (RN-005). None si no existe."""
        fila = (
            (
                await self._con.execute(
                    text(
                        "SELECT tipt_id, tipt_descripcion, tipt_paridad FROM sigat.tipo_trampa"
                        " WHERE tipt_id = :tipt_id"
                    ),
                    {"tipt_id": tipt_id},
                )
            )
            .mappings()
            .first()
        )
        return dict(fila) if fila is not None else None

    async def existe_codigo_trampa(self, tram_codigo: str) -> bool:
        """El código ICA es único en todo el catálogo (``trampa_tram_codigo_key``)."""
        return bool(
            await self._con.scalar(
                text("SELECT EXISTS (SELECT 1 FROM sigat.trampa WHERE tram_codigo = :codigo)"),
                {"codigo": tram_codigo},
            )
        )

    async def predio_del_lote(self, lote_id: int) -> int | None:
        """``ubicacion_trampa.pred_id`` sale del lote (FK compuesta lote_id, pred_id)."""
        valor = await self._con.scalar(
            text("SELECT pred_id FROM sigat.lote WHERE lote_id = :lote_id"), {"lote_id": lote_id}
        )
        return int(valor) if valor is not None else None

    async def distancia_minima_trampas(self, pred_id: int, fecha: date) -> int | None:
        """RN-004: ``prog_distancia_min_trampas_m`` del programa vigente en ``fecha``.

        Vigencia [desde, hasta). None si el predio no tiene programa vigente en esa fecha.
        """
        valor = await self._con.scalar(
            text(
                """
SELECT prog_distancia_min_trampas_m
  FROM sigat.programa_inspeccion
 WHERE pred_id = :pred_id
   AND prog_vigente_desde <= :fecha
   AND (prog_vigente_hasta IS NULL OR prog_vigente_hasta > :fecha)
"""
            ),
            {"pred_id": pred_id, "fecha": fecha},
        )
        return int(valor) if valor is not None else None

    async def trampas_cercanas(
        self,
        pred_id: int,
        lon: Decimal,
        lat: Decimal,
        fecha: date,
        distancia_m: int,
        excluir_tram_id: int | None = None,
    ) -> list[str]:
        """Códigos de las trampas del predio a menos de ``distancia_m`` del punto (RN-004).

        La BD no hace cumplir esta regla para trampas (solo para la retícula), por eso se consulta
        aquí. Cuenta las ubicaciones que siguen vigentes desde ``fecha``. La distancia se mide en
        metros sobre ``geography``. ``excluir_tram_id`` deja fuera a la propia trampa al reubicarla.
        """
        filas = await self._con.execute(
            text(
                """
SELECT t.tram_codigo
  FROM sigat.ubicacion_trampa u
  JOIN sigat.trampa t ON t.tram_id = u.tram_id
 WHERE u.pred_id = :pred_id
   AND (u.ubic_fecha_fin IS NULL OR u.ubic_fecha_fin > :fecha)
   AND (CAST(:excluir AS integer) IS NULL OR u.tram_id <> CAST(:excluir AS integer))
   AND ST_Distance(
         CAST(u.ubic_geom AS geography),
         CAST(ST_SetSRID(ST_MakePoint(CAST(:lon AS float8), CAST(:lat AS float8)), 4326) AS geography)
       ) < :distancia
 ORDER BY t.tram_codigo
"""
            ),
            {
                "pred_id": pred_id,
                "fecha": fecha,
                "excluir": excluir_tram_id,
                "lon": float(lon),
                "lat": float(lat),
                "distancia": distancia_m,
            },
        )
        return [str(f) for f in filas.scalars()]

    async def insertar_trampa(self, datos: dict[str, Any], ubicacion: dict[str, Any]) -> int:
        """trampa + ubicacion_trampa en una transacción (fn_trampa_valida, fn_ubicacion_trampa_valida).

        El SAVEPOINT garantiza que no quede una trampa sin ubicación si la BD rechaza la segunda
        fila. La trampa nace en estado ``activo`` con la fecha de hoy en la zona del predio (ADR-10).
        """
        async with self._con.begin_nested():
            tram_id = await self._con.scalar(
                text(
                    """
INSERT INTO sigat.trampa (tram_codigo, tipt_id, atra_id, esta_id, tram_fecha_registro,
                          tram_tecnico_registro_id)
SELECT :tram_codigo, :tipt_id, :atra_id, e.esta_id,
       CAST(now() AT TIME ZONE p.pred_zona_horaria AS date), :tram_tecnico_registro_id
  FROM sigat.estado_registro e
  JOIN sigat.predio p ON p.pred_id = :pred_id
 WHERE e.esta_descripcion = 'activo'
RETURNING tram_id
"""
                ),
                {**datos, "pred_id": ubicacion["pred_id"]},
            )
            if tram_id is None:
                raise RuntimeError("Falta el estado 'activo' en estado_registro o el predio no existe")
            await self._con.execute(
                text(
                    """
INSERT INTO sigat.ubicacion_trampa (tram_id, pred_id, lote_id, ubic_fecha, tecn_id, vigi_id, ruta_id,
                                    retc_id, loca_id, arbo_id, ubic_long, ubic_lat, ubic_alt,
                                    ubic_referencia)
VALUES (:tram_id, :pred_id, :lote_id, :ubic_fecha, :tecn_id, :vigi_id, :ruta_id,
        :retc_id, :loca_id, :arbo_id, :ubic_long, :ubic_lat, :ubic_alt, :ubic_referencia)
"""
                ),
                {**ubicacion, "tram_id": tram_id},
            )
        return int(tram_id)

    async def actualizar_trampa(self, tram_id: int, cambios: dict[str, Any]) -> None:
        """Aplica solo los campos presentes en ``cambios`` (atra_id, esta_id, tecn_id_estado).

        Un cambio de estado fija ``tram_fecha_estado`` y ``tram_tecnico_estado_id``. Siempre se
        actualiza ``tram_fecha_actualizacion`` con la fecha de hoy en la zona del predio (ADR-10).
        ``tram_tecnico_actualizacion_id`` es el técnico de ESTA actualización: ``tecn_id_estado`` o
        nulo si no vino (por ejemplo, solo cambió el atrayente); no se arrastra el anterior.
        """
        await self._con.execute(
            text(
                """
UPDATE sigat.trampa t
   SET atra_id = CASE WHEN CAST(:cambia_atrayente AS boolean)
                      THEN CAST(:atra_id AS integer) ELSE t.atra_id END,
       esta_id = CASE WHEN CAST(:cambia_estado AS boolean)
                      THEN CAST(:esta_id AS integer) ELSE t.esta_id END,
       tram_fecha_estado = CASE WHEN CAST(:cambia_estado AS boolean)
                                THEN h.hoy ELSE t.tram_fecha_estado END,
       tram_tecnico_estado_id = CASE WHEN CAST(:cambia_estado AS boolean)
                                     THEN CAST(:tecnico AS integer) ELSE t.tram_tecnico_estado_id END,
       tram_tecnico_actualizacion_id = CAST(:tecnico AS integer),
       tram_fecha_actualizacion = h.hoy
  FROM (SELECT COALESCE(
                 (SELECT CAST(now() AT TIME ZONE p.pred_zona_horaria AS date)
                    FROM sigat.ubicacion_trampa u
                    JOIN sigat.predio p ON p.pred_id = u.pred_id
                   WHERE u.tram_id = :tram_id AND u.ubic_fecha_fin IS NULL),
                 CURRENT_DATE) AS hoy) h
 WHERE t.tram_id = :tram_id
"""
            ),
            {
                "tram_id": tram_id,
                "cambia_atrayente": "atra_id" in cambios,
                "atra_id": cambios.get("atra_id"),
                "cambia_estado": "esta_id" in cambios,
                "esta_id": cambios.get("esta_id"),
                "tecnico": cambios.get("tecn_id_estado"),
            },
        )

    async def ubicaciones(self, tram_id: int, usua_id: int) -> list[dict[str, Any]]:
        """Historial de ``ubicacion_trampa``, la más reciente primero.

        Solo las ubicaciones en lotes de ``fn_lotes_visibles``: si la trampa estuvo antes en un
        predio ajeno, esa parte no se muestra (toda consulta de lotes se restringe por asignación).
        Las filas heredadas sin lote (``ubic_origen = 'legado'``) tampoco: el contrato
        ``UbicacionTrampa`` exige ``lote_id``.
        """
        filas = await self._con.execute(
            text(
                """
SELECT ubic_id, tram_id, pred_id, lote_id, ubic_fecha, ubic_fecha_fin, tecn_id, vigi_id, ruta_id,
       retc_id, loca_id, arbo_id, ubic_long, ubic_lat, ubic_alt, ubic_referencia, ubic_origen,
       ubic_registrado_en
  FROM sigat.ubicacion_trampa
 WHERE tram_id = :tram_id
   AND lote_id IN (SELECT lote_id FROM sigat.fn_lotes_visibles(:usua))
 ORDER BY ubic_fecha DESC, ubic_id DESC
"""
            ),
            {"tram_id": tram_id, "usua": usua_id},
        )
        return [dict(f) for f in filas.mappings()]

    async def reubicar(self, tram_id: int, nueva: dict[str, Any]) -> int:
        """Cierra la vigente (ubic_fecha_fin = nueva.ubic_fecha) e inserta la nueva."""
        async with self._con.begin_nested():
            ubic_vigente = await self._con.scalar(
                text(
                    """
SELECT ubic_id
  FROM sigat.ubicacion_trampa
 WHERE tram_id = :tram_id AND ubic_fecha_fin IS NULL
   FOR UPDATE
"""
                ),
                {"tram_id": tram_id},
            )
            if ubic_vigente is not None:
                await self._con.execute(
                    text(
                        """
UPDATE sigat.ubicacion_trampa
   SET ubic_fecha_fin = :ubic_fecha
 WHERE ubic_id = :ubic_vigente
"""
                    ),
                    {"ubic_fecha": nueva["ubic_fecha"], "ubic_vigente": ubic_vigente},
                )
            ubic_id = await self._con.scalar(
                text(
                    """
INSERT INTO sigat.ubicacion_trampa (tram_id, pred_id, lote_id, ubic_fecha, tecn_id, vigi_id, ruta_id,
                                    retc_id, loca_id, arbo_id, ubic_long, ubic_lat, ubic_alt,
                                    ubic_referencia)
VALUES (:tram_id, :pred_id, :lote_id, :ubic_fecha, :tecn_id, :vigi_id, :ruta_id,
        :retc_id, :loca_id, :arbo_id, :ubic_long, :ubic_lat, :ubic_alt, :ubic_referencia)
RETURNING ubic_id
"""
                ),
                {**nueva, "tram_id": tram_id},
            )
            if ubic_id is None:
                raise RuntimeError("No se pudo insertar la nueva ubicación")
        return int(ubic_id)

    # programa / retícula / paquete
    async def programa_vigente(self, pred_id: int, fecha: date) -> dict[str, Any] | None:
        """Vigencia [desde, hasta): ``prog_vigente_desde <= fecha AND (hasta IS NULL OR hasta > fecha)``."""
        sql = """
        SELECT pi.prog_id,
               pi.pred_id,
               pi.prog_version,
               pi.prog_vigente_desde AS vigente_desde,
               pi.prog_vigente_hasta AS vigente_hasta,
               pi.prog_ciclo_inicio AS ciclo_inicio,
               pi.prog_ciclo_fin AS ciclo_fin,
               pi.prog_periodo_lectura_dias AS periodo_lectura_dias,
               pi.prog_distancia_min_trampas_m AS distancia_min_trampas_m,
               pi.reti_id,
               r.reti_resolucion_m AS resolucion_reticula_m,
               pi.prog_proveedor_imagen AS proveedor_imagen,
               pi.prog_umbral_exportacion_mtd AS umbral_exportacion_mtd,
               pi.norm_id,
               n.norm_codigo,
               n.norm_titulo,
               pi.prog_motivo_cambio AS motivo_cambio,
               pi.prog_creado_en AS creado_en,
               pe.pers_nombre || ' ' || pe.pers_apellido AS creado_por
          FROM sigat.programa_inspeccion pi
          LEFT JOIN sigat.reticula r ON r.reti_id = pi.reti_id
          LEFT JOIN sigat.norma n ON n.norm_id = pi.norm_id
          LEFT JOIN sigat.usuario u ON u.usua_id = pi.prog_creado_por
          LEFT JOIN sigat.persona pe ON pe.pers_cc = u.pers_cc
         WHERE pi.pred_id = :pred_id
           AND pi.prog_vigente_desde <= :fecha
           AND (pi.prog_vigente_hasta IS NULL OR pi.prog_vigente_hasta > :fecha)
         ORDER BY pi.prog_version DESC
         LIMIT 1
        """
        fila = (await self._con.execute(text(sql), {"pred_id": pred_id, "fecha": fecha})).mappings().first()
        if not fila:
            return None
        datos = dict(fila)
        prog_id = datos["prog_id"]

        # Escenarios
        sql_esc = """
        SELECT pu.esce_id, et.esce_descripcion AS descripcion, et.esce_orden AS orden,
               pu.prou_mtd_desde AS mtd_desde, pu.prou_mtd_hasta AS mtd_hasta
          FROM sigat.programa_umbral pu
          JOIN sigat.escenario_trampeo et ON et.esce_id = pu.esce_id
         WHERE pu.prog_id = :prog_id
         ORDER BY et.esce_orden
        """
        filas_esc = await self._con.execute(text(sql_esc), {"prog_id": prog_id})
        datos["escenarios"] = [dict(fe) for fe in filas_esc.mappings()]

        # Criterios BPA
        sql_bpa = """
        SELECT crit_id FROM sigat.programa_criterio_bpa WHERE prog_id = :prog_id ORDER BY crit_id
        """
        filas_bpa = await self._con.execute(text(sql_bpa), {"prog_id": prog_id})
        datos["criterios_bpa"] = [int(f[0]) for f in filas_bpa]

        if datos.get("norm_id") is not None:
            datos["norma"] = {
                "norm_id": datos["norm_id"],
                "norm_codigo": datos.pop("norm_codigo"),
                "norm_titulo": datos.pop("norm_titulo"),
            }
        else:
            datos["norma"] = None
            datos.pop("norm_codigo", None)
            datos.pop("norm_titulo", None)

        return datos

    async def versiones_programa(self, pred_id: int) -> list[dict[str, Any]]:
        raise NoImplementado("Versiones del programa")

    async def nueva_version_programa(self, pred_id: int, datos: dict[str, Any], creado_por: int) -> int:
        """Cierra la vigente y crea la siguiente + programa_umbral + programa_criterio_bpa."""
        raise NoImplementado("Nueva versión del programa")

    async def reticula(self, pred_id: int) -> dict[str, Any] | None:
        raise NoImplementado("Retícula")

    async def insertar_reticula(
        self, pred_id: int, resolucion_m: int, corte: date | None, celdas: list[dict[str, Any]]
    ) -> int:
        """La retícula es inmutable (fn_reticula_inmutable): una nueva resolución = nueva retícula."""
        raise NoImplementado("Alta de retícula")

    async def paquete_vigente(self, pred_id: int) -> dict[str, Any] | None:
        sql = """
        SELECT paqu_id, pred_id, paqu_version, prog_id, paqu_hash,
               paqu_generado_en AS generado_en, paqu_invalidado_en AS invalidado_en,
               NULL::integer AS tamano_bytes
          FROM sigat.paquete_campo
         WHERE pred_id = :pred_id AND paqu_invalidado_en IS NULL
         ORDER BY paqu_version DESC
         LIMIT 1
        """
        fila = (await self._con.execute(text(sql), {"pred_id": pred_id})).mappings().first()
        return dict(fila) if fila else None

    async def registrar_paquete(
        self, pred_id: int, prog_id: int, hash_hex: str, generado_en: datetime
    ) -> dict[str, Any]:
        sql = """
        INSERT INTO sigat.paquete_campo (
            pred_id, paqu_version, prog_id, paqu_hash, paqu_generado_en
        ) VALUES (
            :pred_id,
            COALESCE((SELECT max(paqu_version) FROM sigat.paquete_campo WHERE pred_id = :pred_id), 0) + 1,
            :prog_id,
            :hash_hex,
            :generado_en
        )
        RETURNING paqu_id, pred_id, paqu_version, prog_id, paqu_hash, paqu_generado_en AS generado_en,
                  paqu_invalidado_en AS invalidado_en, NULL::integer AS tamano_bytes
        """
        fila = (
            (
                await self._con.execute(
                    text(sql),
                    {
                        "pred_id": pred_id,
                        "prog_id": prog_id,
                        "hash_hex": hash_hex,
                        "generado_en": generado_en,
                    },
                )
            )
            .mappings()
            .one()
        )
        return dict(fila)

    async def invalidar_paquetes(self, pred_id: int, motivo: str) -> None:
        sql = """
        UPDATE sigat.paquete_campo
           SET paqu_invalidado_en = now(),
               paqu_motivo_invalidacion = :motivo
         WHERE pred_id = :pred_id
           AND paqu_invalidado_en IS NULL
        """
        await self._con.execute(text(sql), {"pred_id": pred_id, "motivo": motivo})

    async def catalogos_paquete(self) -> dict[str, list[dict[str, object]]]:
        """Catálogos básicos que requiere el móvil para operar sin conexión."""
        catalogos: dict[str, list[dict[str, object]]] = {}
        tablas = [
            (
                "condicion_trampa",
                "SELECT cond_id, cond_descripcion, cond_activa FROM sigat.condicion_trampa ORDER BY cond_id",
            ),
            (
                "atrayente",
                "SELECT atra_id, atra_descripcion, atra_vida_util_min_sem, atra_vida_util_max_sem FROM sigat.atrayente ORDER BY atra_id",
            ),
            (
                "especie_mosca",
                "SELECT espm_id, espm_descripcion, espm_codigo, espm_tipo FROM sigat.especie_mosca ORDER BY espm_id",
            ),
            (
                "tipo_trampa",
                "SELECT tipt_id, tipt_descripcion, tipt_paridad FROM sigat.tipo_trampa ORDER BY tipt_id",
            ),
            (
                "producto_fitosanitario",
                "SELECT prof_id, prof_nombre, prof_ingrediente_activo, prof_registro, prof_activo FROM sigat.producto_fitosanitario ORDER BY prof_id",
            ),
            (
                "unidad_medida",
                "SELECT unid_id, unid_descripcion, unid_simbolo FROM sigat.unidad_medida ORDER BY unid_id",
            ),
        ]
        for nombre, sql in tablas:
            filas = await self._con.execute(text(sql))
            catalogos[nombre] = [dict(f) for f in filas.mappings()]
        return catalogos

    async def contar_lotes_sobre_umbral(self, usua_id: int) -> int:
        """Cuenta lotes visibles del agronomo cuyo MTD supera el umbral fitosanitario."""
        sql = """
        SELECT count(*)::int
          FROM sigat.v_lote_resumen_app vlra
          JOIN sigat.fn_lotes_visibles(:u) lv ON lv.lote_id = vlra.lote_id
        WHERE vlra.contraste = 'igual_o_superior_umbral'
          OR vlra.escenario IN ('Supresión', 'Área infestada')
        """
        resultado = await self._con.scalar(text(sql), {"u": usua_id})
        return int(resultado or 0)
