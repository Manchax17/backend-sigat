"""Acceso a datos del núcleo de registro. SOLO INSERT en tablas versionadas (ADR-03)."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from sigat_api.compartido.tipos import ResultadoContraste
from sigat_api.modules.registro.dominio import EstadoPrevio

# Columnas que un cliente puede enviar en ``datos``; el resto lo pone el servidor.
# Es una lista blanca: nunca se interpola una clave que venga del cuerpo.
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

# Columnas que el servidor agrega siempre a una lectura (no se aceptan del cliente).
_COLUMNAS_LECTURA_SERVIDOR = {
    "usua_id",
    "disp_id",
    "oper_id",
    "lect_t_observacion",
    "lect_t_captura",
    "lect_deriva_reloj_seg",
}


class RepositorioRegistro:
    def __init__(self, con: AsyncConnection) -> None:
        self._con = con

    # ------------------------------------------------------------------ estado previo
    async def estado_previo(
        self,
        entidad: str,
        registro_id: UUID,
        lote_id: int | None,
        tram_id: int | None,
        t_observacion: datetime,
    ) -> EstadoPrevio:
        """Versión vigente del registro lógico + última t_observacion de la serie + solapamiento (R-T5)."""
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

    # ------------------------------------------------------------------ escritura
    async def insertar_version(self, entidad: str, fila: dict[str, Any]) -> UUID:
        """INSERT en la tabla de la entidad. La BD valida versión n+1 y t_observacion inmutable
        (fn_registro_version_valida) y fija t_sincronizacion."""
        if entidad != "lectura_trampa":
            raise ValueError(f"Entidad versionada no soportada por el sprint: {entidad}")
        columnas = [c for c in fila if c in _COLUMNAS_LECTURA | _COLUMNAS_LECTURA_SERVIDOR]
        if not columnas:
            raise ValueError("La lectura no trae columnas que se puedan insertar")
        lista = ", ".join(columnas)
        valores = ", ".join(f":{c}" for c in columnas)
        # S608: los nombres de columna salen de la lista blanca de módulo (_COLUMNAS_*),
        # nunca del cuerpo de la petición; los valores van siempre parametrizados.
        lect_id = await self._con.scalar(
            text(
                f"INSERT INTO sigat.lectura_trampa ({lista}) VALUES ({valores}) RETURNING lect_id",  # noqa: S608
            ),
            {c: fila[c] for c in columnas},
        )
        return UUID(str(lect_id))

    async def insertar_visita(self, fila: dict[str, Any], lote_ids: list[int]) -> None:
        """visita + visita_lote (todas del mismo predio: FK compuesta)."""
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
        self, visi_id: UUID, estado: str, motivo: str | None, usua_id: int
    ) -> None:
        """Único UPDATE permitido sobre visita (fn_visita_integridad)."""
        await self._con.execute(
            text(
                """
UPDATE sigat.visita
   SET visi_estado_revision = :estado,
       visi_motivo_revision = :motivo,
       visi_revisada_por = :usua,
       visi_revisada_en = CASE WHEN :estado = 'pendiente' THEN NULL ELSE now() END
 WHERE visi_id = :visi
"""
            ),
            {"estado": estado, "motivo": motivo, "usua": usua_id, "visi": visi_id},
        )

    # ------------------------------------------------------------------ consultas de apoyo
    async def lectura_vigente(self, registro_id: UUID) -> dict[str, Any] | None:
        """Columnas de dominio de la última versión (para completar una corrección parcial)."""
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

    # ------------------------------------------------------------------ MTD
    async def lecturas_mtd(self, peri_id: int) -> list[dict[str, Any]]:
        """Lecturas oficiales vigentes del periodo y su inicio de exposición (RF-022)."""
        filas = await self._con.execute(
            text(
                """
WITH peri AS (
    SELECT p.peri_id, p.lote_id, p.peri_inicio, p.peri_fin, l.pred_id
      FROM sigat.periodo_trampeo p
      JOIN sigat.lote l ON l.lote_id = p.lote_id
     WHERE p.peri_id = :peri
), vig AS (
    SELECT DISTINCT ON (lect_registro_id) *
      FROM sigat.lectura_trampa
     ORDER BY lect_registro_id, lect_version DESC
)
SELECT v.lect_id,
       v.tram_id,
       v.lect_especimenes,
       (v.lect_t_observacion AT TIME ZONE p.pred_zona_horaria)::date AS lect_fecha,
       GREATEST(peri.peri_inicio, u.ubic_fecha) AS inicio_exposicion
  FROM vig v
  JOIN peri ON true
  JOIN sigat.predio p ON p.pred_id = peri.pred_id
  JOIN sigat.ubicacion_trampa u ON u.ubic_id = v.ubic_id
 WHERE v.lect_linaje = 'inspeccion_oficial'
   AND v.lect_leida
   AND NOT v.lect_anulada
   AND u.lote_id = peri.lote_id
   AND (v.lect_t_observacion AT TIME ZONE p.pred_zona_horaria)::date
       BETWEEN peri.peri_inicio AND peri.peri_fin
 ORDER BY v.tram_id, v.lect_t_observacion
"""
            ),
            {"peri": peri_id},
        )
        return [dict(f) for f in filas.mappings()]

    async def umbral_vigente(self, peri_id: int) -> dict[str, Any] | None:
        """Umbral del programa con el que se abrió el periodo (no el de hoy)."""
        fila = (
            (
                await self._con.execute(
                    text(
                        """
SELECT pi.prog_id,
       pi.prog_umbral_exportacion_mtd AS umbral,
       pi.norm_id
  FROM sigat.periodo_trampeo p
  JOIN sigat.programa_inspeccion pi ON pi.prog_id = p.prog_id
 WHERE p.peri_id = :peri
"""
                    ),
                    {"peri": peri_id},
                )
            )
            .mappings()
            .first()
        )
        return dict(fila) if fila else None

    async def escenario_para(self, prog_id: int, valor: Decimal) -> int | None:
        """Escenario cuyo rango de MTD contiene ``valor`` (``programa_umbral``)."""
        esce_id = await self._con.scalar(
            text(
                """
SELECT esce_id
  FROM sigat.programa_umbral
 WHERE prog_id = :prog
   AND prou_mtd_desde <= :valor
   AND (prou_mtd_hasta IS NULL OR :valor <= prou_mtd_hasta)
 ORDER BY prou_mtd_desde DESC
 LIMIT 1
"""
            ),
            {"prog": prog_id, "valor": valor},
        )
        return int(esce_id) if esce_id is not None else None

    async def insertar_mtd(
        self,
        *,
        peri_id: int,
        moscas: int,
        trampas: int,
        dias_exposicion: Decimal,
        valor: Decimal | None,
        contraste: ResultadoContraste,
        umbral: dict[str, Any] | None,
        lect_ids: list[UUID],
        motivo: str,
    ) -> int:
        """Inserta la versión n+1 de ``indice_mtd`` (y sus lecturas) si el resultado cambió."""
        actual = (
            (
                await self._con.execute(
                    text(
                        """
SELECT indi_id, indi_version, indi_moscas, indi_trampas, indi_dias_exposicion,
       indi_umbral_aplicado, indi_resultado_contraste, norm_id, esce_id
  FROM sigat.indice_mtd
 WHERE peri_id = :peri AND espm_id IS NULL
 ORDER BY indi_version DESC
 LIMIT 1
"""
                    ),
                    {"peri": peri_id},
                )
            )
            .mappings()
            .first()
        )

        umbral_aplicado = umbral["umbral"] if umbral else None
        norm_id = umbral["norm_id"] if umbral else None
        esce_id = None
        if contraste != ResultadoContraste.SIN_UMBRAL and valor is not None and umbral:
            esce_id = await self.escenario_para(int(umbral["prog_id"]), valor)

        if actual is not None and self._mismo_resultado(
            actual,
            moscas=moscas,
            trampas=trampas,
            dias=dias_exposicion,
            umbral=umbral_aplicado,
            contraste=contraste,
            norm_id=norm_id,
            esce_id=esce_id,
        ):
            return int(actual["indi_id"])

        version = int(actual["indi_version"]) + 1 if actual else 1
        indi_id = await self._con.scalar(
            text(
                """
INSERT INTO sigat.indice_mtd (
    peri_id, espm_id, indi_version, indi_moscas, indi_trampas, indi_dias_exposicion,
    indi_umbral_aplicado, norm_id, indi_resultado_contraste, esce_id, indi_motivo_recalculo
) VALUES (
    :peri, NULL, :version, :moscas, :trampas, :dias,
    :umbral, :norm, :contraste, :esce, :motivo
)
RETURNING indi_id
"""
            ),
            {
                "peri": peri_id,
                "version": version,
                "moscas": moscas,
                "trampas": trampas,
                "dias": dias_exposicion,
                "umbral": umbral_aplicado,
                "norm": norm_id,
                "contraste": str(contraste),
                "esce": esce_id,
                "motivo": motivo if version > 1 else None,
            },
        )
        nuevo = int(indi_id)
        for lect_id in dict.fromkeys(lect_ids):
            await self._con.execute(
                text("INSERT INTO sigat.indice_mtd_lectura (indi_id, lect_id) VALUES (:indi, :lect)"),
                {"indi": nuevo, "lect": lect_id},
            )
        return nuevo

    @staticmethod
    def _mismo_resultado(
        actual: Any,
        *,
        moscas: int,
        trampas: int,
        dias: Decimal,
        umbral: Decimal | None,
        contraste: ResultadoContraste,
        norm_id: int | None,
        esce_id: int | None,
    ) -> bool:
        return (
            int(actual["indi_moscas"]) == moscas
            and int(actual["indi_trampas"]) == trampas
            and Decimal(str(actual["indi_dias_exposicion"])) == Decimal(dias)
            and _decimal_o_none(actual["indi_umbral_aplicado"]) == _decimal_o_none(umbral)
            and str(actual["indi_resultado_contraste"]) == str(contraste)
            and actual["norm_id"] == norm_id
            and actual["esce_id"] == esce_id
        )

    # ------------------------------------------------------------------ resto (fuera del sprint)
    async def visitas(self, usua_id: int, filtros: dict[str, Any], cursor: str | None) -> Any:
        raise NotImplementedError("Visitas")

    async def lecturas(self, usua_id: int, filtros: dict[str, Any], cursor: str | None) -> Any:
        """v_lectura_vigente por defecto; ``incluir_versiones`` lee lectura_trampa completa."""
        raise NotImplementedError("Lecturas")

    async def versiones(self, entidad: str, registro_id: UUID) -> list[dict[str, Any]]:
        raise NotImplementedError("Versiones")

    async def registros_por_revisar(self, usua_id: int, estado: str | None, cursor: str | None) -> Any:
        """v_registro_campo filtrado por predios visibles del agrónomo."""
        raise NotImplementedError("Registros por revisar")

    async def actividad(self, usua_id: int, filtros: dict[str, Any], cursor: str | None) -> Any:
        """fn_actividad_lote(:usua) + hallazgos y etapas."""
        raise NotImplementedError("Actividad")

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


def _decimal_o_none(valor: Any) -> Decimal | None:
    return None if valor is None else Decimal(str(valor))
