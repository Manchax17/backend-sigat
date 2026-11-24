import json
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from sigat_api.compartido.errores import NoImplementado
from sigat_api.modules.registro.dominio import EstadoPrevio

_COLUMNAS_LECTURA = frozenset(
    {
        "lect_registro_id",
        "lect_version",
        "visi_id",
        "tram_id",
        "ubic_id",
        "lect_leida",
        "lect_especimenes",
        "lect_justificacion",
        "cond_id",
        "lect_fecha_cambio_cuerpo",
        "lect_fecha_cambio_atrayente",
        "atra_id",
        "lect_observaciones",
        "lect_precision_m",
        "lect_coincide_lote",
        "lect_linaje",
        "lect_anulada",
        "lect_motivo_anulacion",
        "lect_motivo_correccion",
    }
)

_COLUMNAS_LECTURA_SERVIDOR = {
    "usua_id",
    "disp_id",
    "oper_id",
    "lect_t_observacion",
    "lect_t_captura",
    "lect_deriva_reloj_seg",
}

Clave = tuple[datetime, UUID, int | None]

# ``:u`` = actor, ``:solo_propias`` = el actor es técnico. ``{v}`` = alias de la tabla visita.
_VISITA_VISIBLE = """
(CASE WHEN CAST(:solo_propias AS boolean) THEN {v}.usua_id = :u
      ELSE EXISTS (SELECT 1 FROM sigat.visita_lote vv
                    WHERE vv.visi_id = {v}.visi_id
                      AND vv.lote_id IN (SELECT lote_id FROM sigat.fn_lotes_visibles(:u))) END)
"""

_PREDIO_ACCESIBLE = "{v}.pred_id IN (SELECT pred_id FROM sigat.v_acceso_predio WHERE usua_id = :u)"

# Paginación por clave sobre (instante, identificador) en orden descendente.
_DESPUES_DE = (
    "(CAST(:c_t AS timestamptz) IS NULL OR ({t}, {id}) < (CAST(:c_t AS timestamptz), CAST(:c_id AS uuid)))"
)

# Encabezado común de visita y de registro en revisión. Lotes y trampas como en v_registro_campo.
_COLUMNAS_VISITA = """
SELECT v.visi_id, v.usua_id, pe.pers_nombre || ' ' || pe.pers_apellido AS tecnico,
       v.pred_id, pr.pred_nombre, v.ruta_id,
       v.visi_t_inicio_obs AS t_inicio_obs, v.visi_t_fin_obs AS t_fin_obs,
       v.visi_t_captura AS t_captura, v.visi_t_sincronizacion AS t_sincronizacion,
       v.visi_deriva_reloj_seg AS deriva_reloj_seg,
       v.visi_estado_revision AS estado_revision, v.visi_motivo_revision AS motivo_revision,
       v.visi_revisada_por AS revisada_por, v.visi_revisada_en AS revisada_en,
       COALESCE(lt.lote_ids, '{}') AS lote_ids, COALESCE(lt.lotes, '{}') AS lotes,
       (SELECT count(*) FROM sigat.lectura_trampa x
         WHERE x.visi_id = v.visi_id AND x.lect_version = 1)::int AS trampas_leidas
"""
_FROM_VISITA = """
  FROM sigat.visita v
  JOIN sigat.usuario u ON u.usua_id = v.usua_id
  JOIN sigat.persona pe ON pe.pers_cc = u.pers_cc
  JOIN sigat.predio pr ON pr.pred_id = v.pred_id
  LEFT JOIN LATERAL (
       SELECT array_agg(l.lote_id ORDER BY l.lote_codigo) AS lote_ids,
              array_agg(l.lote_codigo::text ORDER BY l.lote_codigo) AS lotes
         FROM sigat.visita_lote vl JOIN sigat.lote l ON l.lote_id = vl.lote_id
        WHERE vl.visi_id = v.visi_id
  ) lt ON true
"""
_SELECT_VISITA = _COLUMNAS_VISITA + _FROM_VISITA

# Señales para las advertencias de la revisión (el servicio las convierte en texto).
# Solo se concatenan constantes de este módulo (sin datos del usuario): de ahí el noqa.
_SELECT_REVISION = (
    _COLUMNAS_VISITA  # noqa: S608
    + """
, EXISTS (SELECT 1 FROM sigat.v_visita_lote_sin_asignacion s WHERE s.visi_id = v.visi_id)
       AS lote_sin_asignacion
, EXISTS (SELECT 1 FROM sigat.v_lectura_revision r
           WHERE r.visi_id = v.visi_id AND r.lect_coincide_lote IS FALSE) AS trampa_fuera_del_lote
"""
    + _FROM_VISITA
)

# Lectura con los nombres del esquema ``Lectura``. ``{origen}`` es un conjunto de filas de
# lectura_trampa (la vigente, o la última versión de cada registro). El estado de revisión y los
# especímenes originales salen de ``v_lectura_revision``: el estado se DERIVA, no se guarda (BD v9).
_SELECT_LECTURA = """
SELECT l.lect_id, l.lect_registro_id, l.lect_version, l.visi_id, l.tram_id, t.tram_codigo,
       ub.lote_id, l.ubic_id, l.lect_leida, l.lect_especimenes, l.lect_justificacion,
       l.cond_id, c.cond_descripcion AS condicion,
       l.lect_fecha_cambio_cuerpo, l.lect_fecha_cambio_atrayente, l.atra_id, l.lect_observaciones,
       ST_Y(l.lect_geom)::numeric AS latitud, ST_X(l.lect_geom)::numeric AS longitud,
       l.lect_precision_m, l.lect_coincide_lote, l.lect_linaje AS linaje, l.usua_id,
       pe.pers_nombre || ' ' || pe.pers_apellido AS autor,
       l.lect_t_observacion AS t_observacion, l.lect_t_captura AS t_captura,
       l.lect_t_sincronizacion AS t_sincronizacion, l.lect_deriva_reloj_seg AS deriva_reloj_seg,
       l.lect_anulada AS anulada, l.lect_motivo_anulacion AS motivo_anulacion,
       lr.lect_motivo_correccion AS motivo_correccion,
       lr.especimenes_originales, lr.estado_revision,
       (SELECT count(*) FROM sigat.v_evidencia_lectura ev
         WHERE ev.lect_registro_id = l.lect_registro_id)::int AS evidencias
  FROM {origen} l
  JOIN sigat.visita v ON v.visi_id = l.visi_id
  JOIN sigat.trampa t ON t.tram_id = l.tram_id
  JOIN sigat.condicion_trampa c ON c.cond_id = l.cond_id
  JOIN sigat.usuario u ON u.usua_id = l.usua_id
  JOIN sigat.persona pe ON pe.pers_cc = u.pers_cc
  JOIN sigat.v_lectura_revision lr ON lr.lect_id = l.lect_id
  LEFT JOIN sigat.ubicacion_trampa ub ON ub.ubic_id = l.ubic_id
"""

# Última versión de cada lectura de una visita, INCLUIDAS las anuladas: quien revisa necesita
# ver también lo rechazado (v_lectura_vigente las oculta).
_ULTIMA_VERSION_DE_VISITA = """(SELECT DISTINCT ON (lect_registro_id) * FROM sigat.lectura_trampa
          WHERE visi_id = :visi ORDER BY lect_registro_id, lect_version DESC)"""

# Historial unificado del lote. Base: fn_actividad_lote_detalle(:u) (visitas, aplicaciones y cosechas
# vigentes) + la última corrección/anulación de cada lectura + hallazgos y etapas vigentes.
# La visita no tiene linaje en la BD; la hace siempre un técnico, así que es inspeccion_oficial.
_ACTIVIDAD = """
WITH visibles AS (SELECT lote_id FROM sigat.fn_lotes_visibles(:u)),
correcciones AS (
  SELECT DISTINCT ON (lect_registro_id) *
    FROM sigat.lectura_trampa
   WHERE lect_version > 1
   ORDER BY lect_registro_id, lect_version DESC
),
hallazgos AS (
  SELECT DISTINCT ON (hall_registro_id) *
    FROM sigat.hallazgo ORDER BY hall_registro_id, hall_version DESC
),
etapas AS (
  SELECT DISTINCT ON (etap_registro_id) *
    FROM sigat.etapa_declarada ORDER BY etap_registro_id, etap_version DESC
),
actividad AS (
  SELECT d.lote_id, d.lote_descripcion, d.tipo, d.registro_id, d.t_observacion, d.usua_id,
         d.responsable AS autor, d.rol_codigo_responsable AS rol_autor,
         COALESCE(d.linaje, 'inspeccion_oficial')::text AS linaje, d.titulo, d.detalle
    FROM sigat.fn_actividad_lote_detalle(:u) d
  UNION ALL
  SELECT ub.lote_id, lo.lote_descripcion, 'correccion', k.lect_registro_id, k.lect_t_observacion,
         k.usua_id, pe.pers_nombre || ' ' || pe.pers_apellido, r.rol_codigo, k.lect_linaje::text,
         CASE WHEN k.lect_anulada THEN 'Lectura anulada' ELSE 'Lectura corregida' END,
         t.tram_codigo || ' · versión ' || k.lect_version
           || COALESCE(' · ' || COALESCE(k.lect_motivo_anulacion, k.lect_motivo_correccion), '')
    FROM correcciones k
    JOIN sigat.ubicacion_trampa ub ON ub.ubic_id = k.ubic_id
    JOIN sigat.lote lo ON lo.lote_id = ub.lote_id
    JOIN sigat.trampa t ON t.tram_id = k.tram_id
    JOIN sigat.usuario u ON u.usua_id = k.usua_id
    JOIN sigat.persona pe ON pe.pers_cc = u.pers_cc
    JOIN sigat.rol r ON r.rol_id = u.rol_id
   WHERE ub.lote_id IN (SELECT lote_id FROM visibles)
  UNION ALL
  SELECT h.lote_id, lo.lote_descripcion, 'hallazgo', h.hall_registro_id, h.hall_t_observacion,
         h.usua_id, pe.pers_nombre || ' ' || pe.pers_apellido, r.rol_codigo, h.hall_linaje::text,
         'Hallazgo: ' || pl.plag_descripcion, 'Severidad: ' || sv.seve_descripcion
    FROM hallazgos h
    JOIN sigat.lote lo ON lo.lote_id = h.lote_id
    JOIN sigat.plaga_enfermedad pl ON pl.plag_id = h.plag_id
    JOIN sigat.severidad sv ON sv.seve_id = h.seve_id
    JOIN sigat.usuario u ON u.usua_id = h.usua_id
    JOIN sigat.persona pe ON pe.pers_cc = u.pers_cc
    JOIN sigat.rol r ON r.rol_id = u.rol_id
   WHERE NOT h.hall_anulada AND h.lote_id IN (SELECT lote_id FROM visibles)
  UNION ALL
  SELECT e.lote_id, lo.lote_descripcion, 'etapa', e.etap_registro_id, e.etap_t_observacion,
         e.usua_id, pe.pers_nombre || ' ' || pe.pers_apellido, r.rol_codigo, e.etap_linaje::text,
         'Etapa fenológica', f.feno_descripcion
    FROM etapas e
    JOIN sigat.lote lo ON lo.lote_id = e.lote_id
    JOIN sigat.fenologia f ON f.feno_id = e.feno_id
    JOIN sigat.usuario u ON u.usua_id = e.usua_id
    JOIN sigat.persona pe ON pe.pers_cc = u.pers_cc
    JOIN sigat.rol r ON r.rol_id = u.rol_id
   WHERE NOT e.etap_anulada AND e.lote_id IN (SELECT lote_id FROM visibles)
)
SELECT * FROM actividad a
 WHERE (CAST(:lote_id AS integer) IS NULL OR a.lote_id = CAST(:lote_id AS integer))
   AND (CAST(:tipo AS text) IS NULL OR a.tipo = CAST(:tipo AS text))
   AND (CAST(:linaje AS text) IS NULL OR a.linaje = CAST(:linaje AS text))
   AND (CAST(:desde AS timestamptz) IS NULL OR a.t_observacion >= CAST(:desde AS timestamptz))
   AND (CAST(:hasta AS timestamptz) IS NULL OR a.t_observacion <= CAST(:hasta AS timestamptz))
   AND (CAST(:c_t AS timestamptz) IS NULL
        OR (a.t_observacion, a.registro_id, a.lote_id)
           < (CAST(:c_t AS timestamptz), CAST(:c_id AS uuid), CAST(:c_lote AS integer)))
 ORDER BY a.t_observacion DESC, a.registro_id DESC, a.lote_id DESC
 LIMIT :lim
"""


def _clave(despues: Clave | None) -> dict[str, Any]:
    if despues is None:
        return {"c_t": None, "c_id": None, "c_lote": None}
    return {"c_t": despues[0], "c_id": despues[1], "c_lote": despues[2]}


def _json(valor: Any) -> dict[str, Any]:
    """jsonb puede llegar como texto según el códec de la conexión."""
    return json.loads(valor) if isinstance(valor, str) else dict(valor or {})


class RepositorioRegistro:
    def __init__(self, con: AsyncConnection) -> None:
        self._con = con

    async def _filas(self, sql: str, parametros: dict[str, Any]) -> list[dict[str, Any]]:
        resultado = await self._con.execute(text(sql), parametros)
        return [dict(f) for f in resultado.mappings()]

    async def _fila(self, sql: str, parametros: dict[str, Any]) -> dict[str, Any] | None:
        filas = await self._filas(sql, parametros)
        return filas[0] if filas else None

    # ------------------------------------------------------------------ escritura
    async def estado_previo(
        self,
        entidad: str,
        registro_id: UUID,
        lote_id: int | None,
        tram_id: int | None,
        t_observacion: datetime,
    ) -> EstadoPrevio:
        if entidad == "visita":
            existe = await self._con.scalar(
                text("SELECT 1 FROM sigat.visita WHERE visi_id = :v"), {"v": registro_id}
            )
            return EstadoPrevio(
                version_vigente=1 if existe else None,
                t_observacion_original=None,
                ultima_t_observacion_serie=None,
                hay_solapamiento=False,
            )

        if entidad != "lectura_trampa":
            return EstadoPrevio(None, None, None, False)

        vigente = (
            (
                await self._con.execute(
                    text(
                        """
SELECT lect_version, lect_t_observacion
  FROM sigat.lectura_trampa
 WHERE lect_registro_id = :r
 ORDER BY lect_version DESC
 LIMIT 1
"""
                    ),
                    {"r": registro_id},
                )
            )
            .mappings()
            .first()
        )
        if vigente is None:
            return EstadoPrevio(None, None, None, False)

        original = await self._con.scalar(
            text(
                "SELECT lect_t_observacion FROM sigat.lectura_trampa"
                " WHERE lect_registro_id = :r AND lect_version = 1"
            ),
            {"r": registro_id},
        )

        ultima = None
        if tram_id is not None:
            parametros: dict[str, Any] = {"tram": tram_id}
            if lote_id is None:
                consulta_ultima = (
                    "SELECT max(l.lect_t_observacion) FROM sigat.lectura_trampa l"
                    " WHERE l.tram_id = :tram AND NOT l.lect_anulada"
                )
            else:
                parametros["lote"] = lote_id
                consulta_ultima = (
                    "SELECT max(l.lect_t_observacion) FROM sigat.lectura_trampa l"
                    " WHERE l.tram_id = :tram AND NOT l.lect_anulada"
                    "   AND EXISTS (SELECT 1 FROM sigat.ubicacion_trampa u"
                    "                WHERE u.ubic_id = l.ubic_id AND u.lote_id = :lote)"
                )
            ultima = await self._con.scalar(text(consulta_ultima), parametros)

        solapamiento = False
        if tram_id is not None:
            solapamiento = bool(
                await self._con.scalar(
                    text(
                        """
SELECT EXISTS (
    SELECT 1
      FROM sigat.v_lectura_vigente l
     WHERE l.tram_id = :tram
       AND l.lect_registro_id <> :r
       AND (l.lect_t_observacion AT TIME ZONE 'UTC')::date
           = (:t_obs AT TIME ZONE 'UTC')::date
)
"""
                    ),
                    {"tram": tram_id, "r": registro_id, "t_obs": t_observacion},
                )
            )

        return EstadoPrevio(
            version_vigente=int(vigente["lect_version"]),
            t_observacion_original=original,
            ultima_t_observacion_serie=ultima,
            hay_solapamiento=solapamiento,
        )

    async def insertar_version(self, entidad: str, fila: dict[str, Any]) -> UUID:
        if entidad != "lectura_trampa":
            raise ValueError(f"Entidad versionada no soportada por el sprint: {entidad}")
        columnas = [c for c in fila if c in _COLUMNAS_LECTURA | _COLUMNAS_LECTURA_SERVIDOR]
        if not columnas:
            raise ValueError("La lectura no trae columnas que se puedan insertar")
        lista = ", ".join(columnas)
        valores = ", ".join(f":{c}" for c in columnas)
        lect_id = await self._con.scalar(
            text(
                f"INSERT INTO sigat.lectura_trampa ({lista}) VALUES ({valores}) RETURNING lect_id",  # noqa: S608
            ),
            {c: fila[c] for c in columnas},
        )
        return UUID(str(lect_id))

    async def insertar_visita(self, fila: dict[str, Any], lote_ids: list[int]) -> None:
        await self._con.execute(
            text(
                """
INSERT INTO sigat.visita (
    visi_id, disp_id, oper_id, usua_id, pred_id, ruta_id,
    visi_t_inicio_obs, visi_t_fin_obs, visi_t_captura, visi_deriva_reloj_seg
) VALUES (
    :visi_id, :disp_id, :oper_id, :usua_id, :pred_id, :ruta_id,
    :visi_t_inicio_obs, :visi_t_fin_obs, :visi_t_captura, :visi_deriva_reloj_seg
)
"""
            ),
            fila,
        )
        for lote_id in lote_ids:
            await self._con.execute(
                text(
                    "INSERT INTO sigat.visita_lote (visi_id, lote_id, pred_id)"
                    " VALUES (:visi_id, :lote_id, :pred_id)"
                ),
                {"visi_id": fila["visi_id"], "lote_id": lote_id, "pred_id": fila["pred_id"]},
            )

    async def actualizar_revision_visita(
        self, visi_id: UUID, estado: str, motivo: str | None, usua_id: int, en: datetime
    ) -> None:
        """Único UPDATE permitido sobre visita (fn_visita_integridad). ``trg_aud_visita`` deja el
        antes/después en la bitácora con ``sigat.usuario_actual``: esa es la trazabilidad."""
        await self._con.execute(
            text(
                """
UPDATE sigat.visita
   SET visi_estado_revision = :estado,
       visi_motivo_revision = :motivo,
       visi_revisada_por = :usua,
       visi_revisada_en = :en
 WHERE visi_id = :visi
"""
            ),
            {"visi": visi_id, "estado": estado, "motivo": motivo, "usua": usua_id, "en": en},
        )

    async def lectura_vigente(self, registro_id: UUID) -> dict[str, Any] | None:
        fila = (
            (
                await self._con.execute(
                    text(
                        """
SELECT *
  FROM sigat.lectura_trampa
 WHERE lect_registro_id = :r
 ORDER BY lect_version DESC
 LIMIT 1
"""
                    ),
                    {"r": registro_id},
                )
            )
            .mappings()
            .first()
        )
        if fila is None:
            return None
        return {c: fila[c] for c in _COLUMNAS_LECTURA if c in fila}

    async def visita_existe(self, visi_id: UUID) -> bool:
        return bool(
            await self._con.scalar(text("SELECT 1 FROM sigat.visita WHERE visi_id = :v"), {"v": visi_id})
        )

    async def visita_lotes(self, visi_id: UUID) -> list[int]:
        filas = await self._con.execute(
            text("SELECT lote_id FROM sigat.visita_lote WHERE visi_id = :v"), {"v": visi_id}
        )
        return [int(f[0]) for f in filas]

    async def periodo_de(self, lote_id: int, fecha: date) -> dict[str, Any] | None:
        fila = (
            (
                await self._con.execute(
                    text(
                        """
SELECT peri_id, peri_estado
  FROM sigat.periodo_trampeo
 WHERE lote_id = :lote AND peri_inicio <= :fecha AND peri_fin >= :fecha
 ORDER BY peri_fin DESC
 LIMIT 1
"""
                    ),
                    {"lote": lote_id, "fecha": fecha},
                )
            )
            .mappings()
            .first()
        )
        return dict(fila) if fila else None

    # ------------------------------------------------------------------ visitas
    async def visitas(
        self,
        usua_id: int,
        solo_propias: bool,
        filtros: dict[str, Any],
        despues: Clave | None,
        limite: int,
    ) -> list[dict[str, Any]]:
        """Más reciente primero (visi_t_inicio_obs DESC). ``filtros``: pred_id, desde, hasta."""
        sql = (
            _SELECT_VISITA
            + " WHERE "
            + _VISITA_VISIBLE.format(v="v")
            + """
   AND (CAST(:pred_id AS integer) IS NULL OR v.pred_id = CAST(:pred_id AS integer))
   AND (CAST(:desde AS timestamptz) IS NULL OR v.visi_t_inicio_obs >= CAST(:desde AS timestamptz))
   AND (CAST(:hasta AS timestamptz) IS NULL OR v.visi_t_inicio_obs <= CAST(:hasta AS timestamptz))
   AND """
            + _DESPUES_DE.format(t="v.visi_t_inicio_obs", id="v.visi_id")
            + " ORDER BY v.visi_t_inicio_obs DESC, v.visi_id DESC LIMIT :lim"
        )
        return await self._filas(
            sql,
            {
                "u": usua_id,
                "solo_propias": solo_propias,
                "pred_id": filtros.get("pred_id"),
                "desde": filtros.get("desde"),
                "hasta": filtros.get("hasta"),
                "lim": limite,
                **_clave(despues),
            },
        )

    async def visita(self, usua_id: int, solo_propias: bool, visi_id: UUID) -> dict[str, Any] | None:
        sql = _SELECT_VISITA + " WHERE v.visi_id = :visi AND " + _VISITA_VISIBLE.format(v="v")
        return await self._fila(sql, {"u": usua_id, "solo_propias": solo_propias, "visi": visi_id})

    async def lecturas_de_visita(self, visi_id: UUID) -> list[dict[str, Any]]:
        """Última versión de cada lectura de la visita (incluye anuladas), en orden de observación.
        Quien llama ya comprobó que la visita es visible."""
        sql = _SELECT_LECTURA.format(origen=_ULTIMA_VERSION_DE_VISITA) + (
            " ORDER BY l.lect_t_observacion, t.tram_codigo, l.lect_registro_id"
        )
        return await self._filas(sql, {"visi": visi_id})

    # ------------------------------------------------------------------ lecturas
    async def lecturas(
        self,
        usua_id: int,
        solo_propias: bool,
        filtros: dict[str, Any],
        despues: Clave | None,
        limite: int,
    ) -> list[dict[str, Any]]:
        """Lecturas vigentes (``v_lectura_vigente``: última versión no anulada).
        ``filtros``: lote_id, tram_id, desde. Más reciente primero."""
        sql = (
            _SELECT_LECTURA.format(origen="sigat.v_lectura_vigente")
            + " WHERE "
            + _VISITA_VISIBLE.format(v="v")
            + """
   AND (CAST(:lote_id AS integer) IS NULL OR ub.lote_id = CAST(:lote_id AS integer))
   AND (CAST(:tram_id AS integer) IS NULL OR l.tram_id = CAST(:tram_id AS integer))
   AND (CAST(:desde AS timestamptz) IS NULL OR l.lect_t_observacion >= CAST(:desde AS timestamptz))
   AND """
            + _DESPUES_DE.format(t="l.lect_t_observacion", id="l.lect_registro_id")
            + " ORDER BY l.lect_t_observacion DESC, l.lect_registro_id DESC LIMIT :lim"
        )
        return await self._filas(
            sql,
            {
                "u": usua_id,
                "solo_propias": solo_propias,
                "lote_id": filtros.get("lote_id"),
                "tram_id": filtros.get("tram_id"),
                "desde": filtros.get("desde"),
                "lim": limite,
                **_clave(despues),
            },
        )

    async def lectura_visible(self, usua_id: int, solo_propias: bool, registro_id: UUID) -> bool:
        sql = (
            "SELECT EXISTS (SELECT 1 FROM sigat.lectura_trampa l JOIN sigat.visita v ON v.visi_id = l.visi_id"  # noqa: S608
            " WHERE l.lect_registro_id = :reg AND " + _VISITA_VISIBLE.format(v="v") + ")"
        )
        resultado = await self._con.scalar(
            text(sql), {"u": usua_id, "solo_propias": solo_propias, "reg": registro_id}
        )
        return bool(resultado)

    async def versiones(self, entidad: str, registro_id: UUID) -> list[dict[str, Any]]:
        """Todas las versiones de un registro lógico, de la 1 a la vigente. Nada se borra (ADR-03):
        la versión 1 es siempre "lo que se registró originalmente"."""
        if entidad != "lectura_trampa":
            raise NoImplementado(f"Versiones de {entidad}")
        filas = await self._filas(
            """
SELECT l.lect_version AS version, l.lect_anulada AS anulada,
       CASE WHEN l.lect_anulada THEN l.lect_motivo_anulacion ELSE l.lect_motivo_correccion END AS motivo,
       pe.pers_nombre || ' ' || pe.pers_apellido AS autor, l.usua_id,
       l.lect_t_sincronizacion AS t_sincronizacion, l.lect_t_observacion AS t_observacion,
       jsonb_build_object(
    'tram_id', l.tram_id, 'ubic_id', l.ubic_id, 'lect_leida', l.lect_leida,
    'lect_especimenes', l.lect_especimenes, 'lect_justificacion', l.lect_justificacion,
    'cond_id', l.cond_id, 'condicion', c.cond_descripcion,
    'lect_fecha_cambio_cuerpo', l.lect_fecha_cambio_cuerpo,
    'lect_fecha_cambio_atrayente', l.lect_fecha_cambio_atrayente, 'atra_id', l.atra_id,
    'lect_observaciones', l.lect_observaciones, 'lect_precision_m', l.lect_precision_m,
    'lect_coincide_lote', l.lect_coincide_lote,
    'latitud', ST_Y(l.lect_geom), 'longitud', ST_X(l.lect_geom)) AS valores
  FROM sigat.lectura_trampa l
  JOIN sigat.condicion_trampa c ON c.cond_id = l.cond_id
  JOIN sigat.usuario u ON u.usua_id = l.usua_id
  JOIN sigat.persona pe ON pe.pers_cc = u.pers_cc
 WHERE l.lect_registro_id = :reg
 ORDER BY l.lect_version
""",
            {"reg": registro_id},
        )
        for fila in filas:
            fila["valores"] = _json(fila["valores"])
        return filas

    # ------------------------------------------------------------------ revisión del agrónomo
    async def registros_por_revisar(
        self, usua_id: int, estado: str | None, despues: Clave | None, limite: int
    ) -> list[dict[str, Any]]:
        """Visitas de los predios del agrónomo. Sin ``estado``: las que faltan por resolver
        (pendiente y en_revision), lo mismo que ``v_registro_campo``. Más reciente primero."""
        sql = (
            _SELECT_REVISION
            + " WHERE "
            + _PREDIO_ACCESIBLE.format(v="v")
            + """
   AND (CASE WHEN CAST(:estado AS text) IS NULL THEN v.visi_estado_revision <> 'aceptado'
             ELSE v.visi_estado_revision = CAST(:estado AS text) END)
   AND """
            + _DESPUES_DE.format(t="v.visi_t_inicio_obs", id="v.visi_id")
            + " ORDER BY v.visi_t_inicio_obs DESC, v.visi_id DESC LIMIT :lim"
        )
        return await self._filas(sql, {"u": usua_id, "estado": estado, "lim": limite, **_clave(despues)})

    async def registro_revision(
        self, usua_id: int, visi_id: UUID, bloquear: bool = False
    ) -> dict[str, Any] | None:
        """Una visita de los predios del agrónomo, en cualquier estado. ``bloquear`` = FOR UPDATE
        para que dos agrónomos no resuelvan la misma visita a la vez."""
        sql = _SELECT_REVISION + " WHERE v.visi_id = :visi AND " + _PREDIO_ACCESIBLE.format(v="v")
        if bloquear:
            sql += " FOR UPDATE OF v"
        return await self._fila(sql, {"u": usua_id, "visi": visi_id})

    # ------------------------------------------------------------------ historial / actividad
    async def lote_visible(self, usua_id: int, lote_id: int) -> bool:
        resultado = await self._con.scalar(
            text("SELECT EXISTS (SELECT 1 FROM sigat.fn_lotes_visibles(:u) WHERE lote_id = :lote)"),
            {"u": usua_id, "lote": lote_id},
        )
        return bool(resultado)

    async def actividad(
        self, usua_id: int, filtros: dict[str, Any], despues: Clave | None, limite: int
    ) -> list[dict[str, Any]]:
        """Historial unificado de los lotes visibles. ``filtros``: lote_id, tipo, linaje, desde,
        hasta. Orden t_observacion DESC (R-T1 invertido para pantalla); una visita a varios lotes
        sale una vez por lote, por eso la clave incluye lote_id."""
        return await self._filas(
            _ACTIVIDAD,
            {
                "u": usua_id,
                "lote_id": filtros.get("lote_id"),
                "tipo": filtros.get("tipo"),
                "linaje": filtros.get("linaje"),
                "desde": filtros.get("desde"),
                "hasta": filtros.get("hasta"),
                "lim": limite,
                **_clave(despues),
            },
        )

    async def actividad_reciente(self, usua_id: int, lote_id: int, limite: int) -> list[dict[str, Any]]:
        """Últimos eventos del lote: visitas, datos recibidos del campo, lecturas vigentes y
        aplicaciones vigentes. Quien llama ya comprobó que el lote es visible."""
        return await self._filas(
            """
SELECT * FROM (
  SELECT 'visita' AS tipo,
         'Visita de ' || pe.pers_nombre || ' ' || pe.pers_apellido AS texto,
         v.visi_t_inicio_obs AS t
    FROM sigat.visita v
    JOIN sigat.visita_lote vl ON vl.visi_id = v.visi_id
    JOIN sigat.usuario u ON u.usua_id = v.usua_id
    JOIN sigat.persona pe ON pe.pers_cc = u.pers_cc
   WHERE vl.lote_id = :lote
  UNION ALL
  SELECT 'sincronizacion', 'Datos de campo recibidos en el servidor', v.visi_t_sincronizacion
    FROM sigat.visita v
    JOIN sigat.visita_lote vl ON vl.visi_id = v.visi_id
   WHERE vl.lote_id = :lote
  UNION ALL
  SELECT 'lectura',
         'Trampa ' || t.tram_codigo || ': '
           || CASE WHEN l.lect_leida THEN COALESCE(l.lect_especimenes, 0) || ' especímenes'
                   ELSE 'no leída' END,
         l.lect_t_observacion
    FROM sigat.v_lectura_vigente l
    JOIN sigat.ubicacion_trampa ub ON ub.ubic_id = l.ubic_id
    JOIN sigat.trampa t ON t.tram_id = l.tram_id
   WHERE ub.lote_id = :lote
  UNION ALL
  SELECT 'aplicacion', 'Aplicación: ' || COALESCE(d.detalle, 'sin producto'), d.t_observacion
    FROM sigat.fn_actividad_lote_detalle(:u) d
   WHERE d.lote_id = :lote AND d.tipo = 'aplicacion'
) x
ORDER BY t DESC
LIMIT :lim
""",
            {"u": usua_id, "lote": lote_id, "lim": limite},
        )

    async def autorreportes(self, entidad: str, usua_id: int, lote_id: int | None, cursor: str | None) -> Any:
        raise NotImplementedError("Autorreportes")

    async def contar_por_revisar(self, usua_id: int) -> int:
        sql = """
        SELECT count(*)::int
         FROM sigat.v_registro_campo vrc
         JOIN sigat.v_acceso_predio vap ON vap.pred_id = vrc.pred_id
        WHERE vap.usua_id = :u
        """
        resultado = await self._con.scalar(text(sql), {"u": usua_id})
        return int(resultado or 0)
