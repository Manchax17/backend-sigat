"""VALIDADOR DE INVARIANTES (Arquitectura §6.3). Descarta lo mal formado ANTES de tocar el historico.

Se valida en el cliente la FORMA y aquí, otra vez, el DOMINIO (§5.3): el dispositivo
puede estar desactualizado respecto a la configuración vigente.
"""

from datetime import datetime, timedelta
from uuid import UUID

from sigat_api.compartido.contratos import OperacionCampo
from sigat_api.compartido.tipos import AccionSync, Actor, EntidadSync, Linaje
from sigat_api.modules.registro.dominio import MotivoRechazo
from sigat_api.modules.registro.puertos import PuertoConfiguracion
from sigat_api.modules.registro.repositorio import RepositorioRegistro

# Margen para no rechazar un teléfono que va unos segundos adelantado.
_TOLERANCIA_RELOJ = timedelta(minutes=5)

_LINAJE_POR_ROL = {
    "TECNICO": Linaje.INSPECCION_OFICIAL,
    "AGRONOMO_SUPERVISOR": Linaje.INSPECCION_OFICIAL,
    "PRODUCTOR": Linaje.AUTORREPORTE,
    "ADMINISTRADOR": Linaje.AUTORREPORTE,
}


class ValidadorInvariantes:
    def __init__(self, configuracion: PuertoConfiguracion, repo: RepositorioRegistro) -> None:
        self._configuracion = configuracion
        self._repo = repo  # periodo_trampeo es de este módulo

    async def validar(
        self, op: OperacionCampo, ahora_servidor: datetime, actor: Actor
    ) -> MotivoRechazo | None:
        """Devuelve el primer motivo de rechazo o None.

        Por entidad (mínimo):
        * Todas: t_observacion <= t_captura <= ahora + tolerancia; linaje permitido para el rol
          (TECNICO -> inspeccion_oficial, PRODUCTOR -> autorreporte); el lote es visible para el actor.
        * lectura_trampa: la trampa pertenecía al lote en t_observacion; el lote estaba activo;
          t_observacion dentro de un periodo de trampeo del lote; especímenes >= 0 y nulos si
          lect_leida = false; justificación obligatoria si no se leyó; la visita existe
          (o vino antes en el mismo lote de operaciones).
        * aplicacion_fitosanitaria / cosecha / planificacion_lote: fuera del alcance del sprint 2.
        """
        if op.entidad not in (EntidadSync.VISITA, EntidadSync.LECTURA_TRAMPA):
            return MotivoRechazo.VALOR_NO_VALIDO

        if op.t_observacion > ahora_servidor + _TOLERANCIA_RELOJ:
            return MotivoRechazo.FECHA_FUTURA
        if op.t_captura < op.t_observacion:
            return MotivoRechazo.VALOR_NO_VALIDO

        linaje = op.linaje or _LINAJE_POR_ROL.get(str(actor.rol))
        if linaje is None or linaje != _LINAJE_POR_ROL.get(str(actor.rol)):
            return MotivoRechazo.LINAJE_NO_PERMITIDO

        if op.entidad == EntidadSync.VISITA:
            return await self._validar_visita(op, actor)
        return await self._validar_lectura(op, actor)

    # ------------------------------------------------------------------ visita
    async def _validar_visita(self, op: OperacionCampo, actor: Actor) -> MotivoRechazo | None:
        try:
            pred_id = int(op.datos["pred_id"])
            lotes = [int(x) for x in op.datos["lote_ids"]]
            inicio = _instante(op.datos["visi_t_inicio_obs"])
            fin = _instante(op.datos["visi_t_fin_obs"])
        except (KeyError, TypeError, ValueError):
            return MotivoRechazo.VALOR_NO_VALIDO

        if not lotes:
            return MotivoRechazo.VALOR_NO_VALIDO
        if inicio > fin or fin > op.t_captura:
            return MotivoRechazo.VALOR_NO_VALIDO
        if op.version != 1:
            # La visita no es versionada: se crea una vez y solo se revisa.
            return MotivoRechazo.VERSION_INVALIDA

        for lote_id in lotes:
            if await self._configuracion.lote_predio(lote_id) != pred_id:
                return MotivoRechazo.SIN_PERMISO_SOBRE_LOTE
            if not await self._configuracion.lote_visible(actor.usua_id, lote_id):
                return MotivoRechazo.SIN_PERMISO_SOBRE_LOTE
            if not await self._configuracion.lote_activo_en(lote_id, op.t_observacion):
                return MotivoRechazo.LOTE_INACTIVO
        return None

    # ------------------------------------------------------------------ lectura
    async def _validar_lectura(self, op: OperacionCampo, actor: Actor) -> MotivoRechazo | None:
        datos = op.datos
        try:
            visi_id = UUID(str(datos["visi_id"]))
            tram_id = int(datos["tram_id"])
            cond_id = int(datos["cond_id"])
            leida = datos["lect_leida"]
        except (KeyError, TypeError, ValueError):
            return MotivoRechazo.VALOR_NO_VALIDO
        if not isinstance(leida, bool) or cond_id not in (1, 2):
            return MotivoRechazo.VALOR_NO_VALIDO

        if not await self._repo.visita_existe(visi_id):
            # Puede venir después en el mismo lote: se reenvía en orden.
            return MotivoRechazo.REFERENCIA_PENDIENTE

        pertenencia = await self._configuracion.pertenencia_trampa(tram_id, op.t_observacion)
        if pertenencia is None or pertenencia.lote_id is None:
            return MotivoRechazo.TRAMPA_NO_PERTENECE_AL_LOTE
        if not pertenencia.lote_activo:
            return MotivoRechazo.LOTE_INACTIVO
        if pertenencia.lote_id not in await self._repo.visita_lotes(visi_id):
            return MotivoRechazo.TRAMPA_NO_PERTENECE_AL_LOTE
        if not await self._configuracion.lote_visible(actor.usua_id, pertenencia.lote_id):
            return MotivoRechazo.SIN_PERMISO_SOBRE_LOTE

        periodo = await self._repo.periodo_de(pertenencia.lote_id, op.t_observacion.date())
        if periodo is None:
            return MotivoRechazo.FUERA_DE_PERIODO

        if leida:
            especimenes = datos.get("lect_especimenes")
            if isinstance(especimenes, bool) or not isinstance(especimenes, int) or especimenes < 0:
                return MotivoRechazo.VALOR_NO_VALIDO
        else:
            if datos.get("lect_especimenes") is not None:
                return MotivoRechazo.VALOR_NO_VALIDO
            justificacion = datos.get("lect_justificacion")
            if not isinstance(justificacion, str) or not justificacion.strip():
                return MotivoRechazo.VALOR_NO_VALIDO

        if op.accion == AccionSync.ANULAR:
            motivo = datos.get("lect_motivo_anulacion")
            if not isinstance(motivo, str) or not motivo.strip():
                return MotivoRechazo.VALOR_NO_VALIDO
        elif op.accion == AccionSync.CORREGIR and op.version > 1:
            motivo = datos.get("lect_motivo_correccion")
            if not isinstance(motivo, str) or not motivo.strip():
                return MotivoRechazo.VALOR_NO_VALIDO
        return None


def _instante(valor: object) -> datetime:
    if isinstance(valor, datetime):
        return valor
    return datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
