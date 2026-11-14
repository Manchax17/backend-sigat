"""Receptor, clasificador y encolador (Arquitectura §6.3)."""

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from sigat_api.compartido.contratos import OperacionCampo, ResultadoAplicacion
from sigat_api.compartido.errores import NoImplementado, Prohibido
from sigat_api.compartido.tipos import (
    AccionSync,
    Actor,
    ClasificacionOperacion,
    EntidadSync,
    ResultadoSync,
)
from sigat_api.config import Configuracion
from sigat_api.infra.cola.cola import TAREA_REPROCESAR_PERIODO, Cola
from sigat_api.infra.reloj import Reloj
from sigat_api.modules.sincronizacion import esquemas as e
from sigat_api.modules.sincronizacion import huella
from sigat_api.modules.sincronizacion.puertos import PuertoRegistro, PuertoResultadoPlanificacion
from sigat_api.modules.sincronizacion.repositorio import RepositorioSincronizacion

# Claves que el servidor asigna y que el cliente nunca debe enviar en ``datos``.
_CLAVES_PROHIBIDAS = frozenset({"usua_id", "disp_id", "oper_id"})
_SUFIJO_T_SINCRONIZACION = "_t_sincronizacion"


class _Reclasificar(Exception):
    """Señal interna: el dominio decidió una clasificación distinta de la esperada.

    Sale del SAVEPOINT para deshacer la fila de ``operacion_sync`` y el registro de dominio
    que se hubieran insertado con la clasificación provisional; se reinsertan con la final.
    """

    def __init__(self, resultado: ResultadoAplicacion) -> None:
        super().__init__("reclasificar")
        self.resultado = resultado


class ServicioSincronizacion:
    def __init__(
        self,
        repo: RepositorioSincronizacion,
        registro: PuertoRegistro,
        planificacion: PuertoResultadoPlanificacion,
        cola: Cola,
        reloj: Reloj,
        cfg: Configuracion,
    ) -> None:
        self._repo = repo
        self._registro = registro
        self._planificacion = planificacion
        self._cola = cola
        self._reloj = reloj
        self._cfg = cfg

    async def recibir(self, actor: Actor, lote: e.LoteSincronizacion) -> e.RespuestaLote:
        """Procesa el lote EN ORDEN, cada operación en su propio SAVEPOINT (una que falla no
        tumba a las demás; el resultado parcial es normal, ADR-04).

        Por operación:
        1. ``lote.disp_id`` == ``actor.disp_id`` y dispositivo activo; si no -> 403 para TODO el lote.
        2. Recalcular ``hash_contenido`` (huella.py); si no coincide con el enviado -> rechazada
           ``HUELLA_INVALIDA`` (el cliente serializó distinto: error de programación, no de usuario).
        3. Idempotencia R-T4: ¿existe (disp_id, cliente_id)? mismo hash -> ``idempotente_repetida``
           señalando la operación original (sin duplicarla); distinto -> ``conflicto_contenido``.
           Ambos en recepcion_sync.
        4. R-T6: |deriva| > SIGAT_SYNC_UMBRAL_DERIVA_RELOJ_SEG y no ``confirma_deriva_reloj`` -> ``revisar``.
        5. Insertar operacion_sync y delegar en Registro (o Planificación para planificacion_lote).
        6. Por cada periodo afectado: encolar ``reprocesar_periodo`` con clave única por peri_id
           (ADR-12: el dato primero; el reproceso no bloquea la respuesta).
        7. Responder con ``reintentable=false`` salvo error técnico (excepción no de dominio).
        Al final: actualizar disp_ultima_sync (disparador fn_dispositivo_sync) y devolver cursor.
        """
        await self._verificar_dispositivo(actor, lote)

        ahora = self._reloj.ahora()
        resultados: list[e.ResultadoOperacion] = []
        for op in lote.operaciones:
            try:
                resultados.append(await self._procesar(actor, lote, op, ahora))
            except Exception:
                # Error técnico: el SAVEPOINT ya revirtió lo que se hubiera escrito. Se responde
                # para esa operación y el cliente puede reintentar el resto del lote.
                resultados.append(
                    self._resultado(
                        op,
                        oper_id=None,
                        resultado=ResultadoSync.RECHAZADA,
                        motivo="No se pudo guardar la operación; vuelva a intentarlo.",
                        codigo="ERROR_TECNICO",
                        reintentable=True,
                    )
                )

        return e.RespuestaLote(
            resultados=resultados,
            cursor=ahora.isoformat(),
            servidor_utc=ahora,
        )

    async def pendientes(
        self, actor: Actor, clasificaciones: list[ClasificacionOperacion]
    ) -> list[e.OperacionPendiente]:
        filas = await self._repo.pendientes_de_usuario(actor.usua_id, [c.value for c in clasificaciones])
        return [e.OperacionPendiente(**fila) for fila in filas]

    async def resolver(self, actor: Actor, oper_id: int, s: e.ResolucionOperacion) -> e.ResultadoOperacion:
        """Solo el autor de la operación o un agrónomo del predio. 'confirmar' aplica el payload
        guardado como alta; 'descartar' deja constancia. Ambas crean operación con oper_resuelve_oper_id."""
        raise NoImplementado("Resolución de operación")

    async def cambios(self, actor: Actor, desde: str | None, limite: int) -> e.Cambios:
        raise NoImplementado("Cambios del servidor")

    # ---------------------------------------------------------------- operación
    async def _verificar_dispositivo(self, actor: Actor, lote: e.LoteSincronizacion) -> None:
        if actor.disp_id is None or lote.disp_id != actor.disp_id:
            raise Prohibido(
                "El lote no corresponde al dispositivo de la sesión.",
                codigo="DISPOSITIVO_NO_COINCIDE",
            )
        dispositivo = await self._repo.dispositivo(lote.disp_id)
        if dispositivo is None or not dispositivo.get("disp_activo"):
            raise Prohibido(
                "Este dispositivo fue dado de baja. Contacte al administrador.",
                codigo="DISPOSITIVO_INACTIVO",
            )

    async def _procesar(
        self, actor: Actor, lote: e.LoteSincronizacion, op: e.OperacionSync, ahora: datetime
    ) -> e.ResultadoOperacion:
        # 1. Huella: el cliente debe serializar exactamente el mismo contenido canónico.
        if huella.calcular(huella.contenido_canonico(op)) != op.hash_contenido:
            return self._resultado(
                op,
                oper_id=None,
                resultado=ResultadoSync.RECHAZADA,
                motivo="La huella del contenido no coincide con lo enviado; reenvíe la operación.",
                codigo="HUELLA_INVALIDA",
            )

        # 2. Idempotencia R-T4: (disp_id, cliente_id) es la clave.
        existente = await self._repo.operacion_existente(lote.disp_id, op.cliente_id)
        if existente is not None:
            hash_original = str(existente["oper_hash_contenido"]).strip()
            if hash_original == op.hash_contenido:
                await self._repo.registrar_reenvio(
                    int(existente["oper_id"]), op.hash_contenido, ResultadoSync.IDEMPOTENTE_REPETIDA.value
                )
                return self._resultado(
                    op,
                    oper_id=int(existente["oper_id"]),
                    resultado=ResultadoSync.IDEMPOTENTE_REPETIDA,
                    motivo=existente.get("oper_motivo"),
                    registro_id=UUID(str(existente["oper_registro_id"])),
                    version=int(existente["oper_version"]),
                )
            await self._repo.registrar_reenvio(
                int(existente["oper_id"]), op.hash_contenido, ResultadoSync.CONFLICTO_CONTENIDO.value
            )
            return self._resultado(
                op,
                oper_id=int(existente["oper_id"]),
                resultado=ResultadoSync.CONFLICTO_CONTENIDO,
                motivo="Ya existe una operación con el mismo identificador y contenido distinto.",
                codigo="CONFLICTO_CONTENIDO",
                registro_id=UUID(str(existente["oper_registro_id"])),
                version=int(existente["oper_version"]),
            )

        deriva = self._deriva(lote, op, ahora)

        # 3. R-T6: una deriva grande sin confirmación explícita no se aplica; queda para revisión.
        if abs(deriva) > self._cfg.sync_umbral_deriva_reloj_seg and not op.confirma_deriva_reloj:
            return await self._persistir_decision(
                actor,
                lote.disp_id,
                op,
                deriva,
                ClasificacionOperacion.REVISAR,
                "La hora del dispositivo no coincide con la del servidor. Confirme el envío o ajuste el reloj.",
                "DERIVA_RELOJ",
            )

        # 4. Coherencia mínima que la BD también exige (se rechaza antes de tocar el histórico).
        problema = _validar_datos(op)
        if problema is not None:
            motivo, codigo = problema
            return await self._persistir_decision(
                actor,
                lote.disp_id,
                op,
                deriva,
                ClasificacionOperacion.RECHAZADA,
                motivo,
                codigo,
            )

        # 5. Insertar la operación y delegar el motor temporal.
        return await self._aplicar(actor, lote.disp_id, op, deriva)

    def _deriva(self, lote: e.LoteSincronizacion, op: e.OperacionSync, ahora: datetime) -> int:
        """Deriva del reloj del dispositivo, en segundos (R-T6).

        Se usa la medida que envía el móvil; si no viene, se compara la hora de envío del lote
        con la del servidor. El signo no importa: se compara en valor absoluto.
        """
        if op.deriva_reloj_seg is not None:
            return op.deriva_reloj_seg
        enviado = lote.enviado_en
        if enviado.tzinfo is None:
            enviado = enviado.replace(tzinfo=UTC)
        return round((enviado - ahora).total_seconds())

    async def _persistir_decision(
        self,
        actor: Actor,
        disp_id: UUID,
        op: e.OperacionSync,
        deriva: int,
        clasificacion: ClasificacionOperacion,
        motivo: str,
        codigo: str,
    ) -> e.ResultadoOperacion:
        """Guarda una operación que no se aplica (revisar/rechazada), con su payload."""
        async with self._repo.punto_de_guarda():
            oper_id = await self._insertar(actor, disp_id, op, clasificacion, motivo, deriva, op.datos)
        return self._resultado(
            op,
            oper_id=oper_id,
            resultado=ResultadoSync(clasificacion.value),
            motivo=motivo,
            codigo=codigo,
        )

    async def _aplicar(
        self, actor: Actor, disp_id: UUID, op: e.OperacionSync, deriva: int
    ) -> e.ResultadoOperacion:
        """Inserta la operación con una clasificación provisional y deja que el dominio decida.

        La tabla ``operacion_sync`` es append-only y el registro de dominio referencia
        ``oper_id``; por eso, si el dominio devuelve otra clasificación, se revierte el SAVEPOINT
        y se reinserta con la definitiva. Solo se vuelve a delegar cuando la clasificación final
        implica escribir dominio (alta/corrección); ``revisar`` y ``rechazada`` solo guardan payload.
        """
        esperada = (
            ClasificacionOperacion.ALTA_NUEVA
            if op.accion == AccionSync.CREAR
            else ClasificacionOperacion.CORRECCION
        )
        try:
            async with self._repo.punto_de_guarda():
                oper_id = await self._insertar(actor, disp_id, op, esperada, None, deriva, None)
                resultado = await self._delegar(actor, disp_id, op, oper_id)
                if resultado.clasificacion != esperada:
                    raise _Reclasificar(resultado)
                reproceso, fecha_efecto = await self._encolar(oper_id, resultado)
        except _Reclasificar as cambio:
            resultado = cambio.resultado
            guarda_payload = resultado.clasificacion in (
                ClasificacionOperacion.REVISAR,
                ClasificacionOperacion.RECHAZADA,
            )
            async with self._repo.punto_de_guarda():
                oper_id = await self._insertar(
                    actor,
                    disp_id,
                    op,
                    resultado.clasificacion,
                    resultado.motivo,
                    deriva,
                    op.datos if guarda_payload else None,
                )
                if not guarda_payload:
                    confirmado = await self._delegar(actor, disp_id, op, oper_id)
                    if confirmado.clasificacion != resultado.clasificacion:
                        raise RuntimeError(
                            "El dominio cambió de clasificación al reaplicar la operación"
                        ) from None
                    resultado = confirmado
                reproceso, fecha_efecto = await self._encolar(oper_id, resultado)

        if reproceso == "encolado" and fecha_efecto is None:
            fecha_efecto = op.t_observacion
        return self._resultado(
            op,
            oper_id=oper_id,
            resultado=ResultadoSync(resultado.clasificacion.value),
            motivo=resultado.motivo,
            codigo=resultado.codigo,
            reproceso=reproceso,
            fecha_efecto=fecha_efecto,
        )

    async def _insertar(
        self,
        actor: Actor,
        disp_id: UUID,
        op: e.OperacionSync,
        clasificacion: ClasificacionOperacion,
        motivo: str | None,
        deriva: int,
        payload: dict[str, Any] | None,
    ) -> int:
        return await self._repo.insertar_operacion(
            {
                "disp_id": disp_id,
                "cliente_id": op.cliente_id,
                "usua_id": actor.usua_id,
                "entidad": op.entidad,
                "accion": op.accion,
                "registro_id": op.registro_id,
                "version": op.version,
                "hash": op.hash_contenido,
                "clasificacion": clasificacion,
                "motivo": motivo,
                "t_captura": op.t_captura,
                "deriva": deriva,
                "payload": payload,
            }
        )

    async def _delegar(
        self, actor: Actor, disp_id: UUID, op: e.OperacionSync, oper_id: int
    ) -> ResultadoAplicacion:
        campo = OperacionCampo(
            oper_id=oper_id,
            cliente_id=op.cliente_id,
            disp_id=disp_id,
            entidad=op.entidad,
            accion=op.accion,
            registro_id=op.registro_id,
            version=op.version,
            linaje=op.linaje,
            t_observacion=op.t_observacion,
            t_captura=op.t_captura,
            deriva_reloj_seg=op.deriva_reloj_seg,
            datos=op.datos,
        )
        if op.entidad == EntidadSync.PLANIFICACION_LOTE:
            return await self._planificacion.aplicar_resultado_lote(actor, campo)
        return await self._registro.aplicar(actor, campo)

    async def _encolar(
        self, oper_id: int, resultado: ResultadoAplicacion
    ) -> tuple[Literal["encolado", "no_aplica"], datetime | None]:
        periodos = list(dict.fromkeys(resultado.periodos_afectados))
        if not periodos:
            return "no_aplica", None
        for peri_id in periodos:
            await self._cola.encolar(
                TAREA_REPROCESAR_PERIODO,
                {"peri_id": peri_id, "motivo": "sincronizacion", "oper_id": oper_id},
                clave_unica=f"reprocesar_periodo:{peri_id}",
            )
        return "encolado", resultado.fecha_efecto

    def _resultado(
        self,
        op: e.OperacionSync,
        *,
        oper_id: int | None,
        resultado: ResultadoSync,
        motivo: str | None = None,
        codigo: str | None = None,
        registro_id: UUID | None = None,
        version: int | None = None,
        reproceso: Literal["encolado", "no_aplica"] = "no_aplica",
        fecha_efecto: datetime | None = None,
        reintentable: bool = False,
    ) -> e.ResultadoOperacion:
        return e.ResultadoOperacion(
            cliente_id=op.cliente_id,
            oper_id=oper_id,
            resultado=resultado,
            motivo=motivo,
            codigo=codigo,
            registro_id=registro_id or op.registro_id,
            version=version or op.version,
            reproceso=reproceso,
            fecha_efecto=fecha_efecto,
            reintentable=reintentable,
        )


def _validar_datos(op: e.OperacionSync) -> tuple[str, str] | None:
    """Validaciones que el servidor puede hacer sin consultar la BD.

    Devuelve ``(motivo, codigo)`` para una rechazada persistida, o ``None`` si la operación
    puede delegarse al motor temporal.
    """
    for clave in op.datos:
        if clave in _CLAVES_PROHIBIDAS or clave.endswith(_SUFIJO_T_SINCRONIZACION):
            return (
                f"El campo «{clave}» lo asigna el servidor y no debe enviarse en los datos.",
                "CAMPO_PROHIBIDO",
            )

    if op.entidad != EntidadSync.LECTURA_TRAMPA:
        return None

    # En una anulación el payload puede no repetir el conteo; la coherencia se exige cuando
    # el cliente informa si la trampa fue leída.
    if "lect_leida" not in op.datos:
        return None
    if not isinstance(op.datos["lect_leida"], bool):
        return ("El campo «lect_leida» debe ser verdadero o falso.", "VALOR_NO_VALIDO")

    if op.datos["lect_leida"]:
        especimenes = op.datos.get("lect_especimenes")
        if isinstance(especimenes, bool) or not isinstance(especimenes, int) or especimenes < 0:
            return (
                "Una trampa leída debe indicar los especímenes capturados (0 o más).",
                "VALOR_NO_VALIDO",
            )
    else:
        if op.datos.get("lect_especimenes") is not None:
            return (
                "Una trampa no leída no puede registrar especímenes: use leída con 0 para «cero moscas».",
                "VALOR_NO_VALIDO",
            )
        justificacion = op.datos.get("lect_justificacion")
        if not isinstance(justificacion, str) or not justificacion.strip():
            return ("Una trampa no leída exige una justificación.", "VALOR_NO_VALIDO")

    return None


class RegistradorOperacionesEnLinea:
    """Implementa ``registro.puertos.PuertoOperaciones``: las escrituras EN LÍNEA (web) también
    necesitan su fila en ``operacion_sync`` (la BD exige ``oper_id`` y ``disp_id``). Usa el
    dispositivo 'otro' registrado en el login de la sesión web; ``cliente_id`` = uuid4 nuevo y
    la clasificación la decide Registro igual que en el sync."""

    def __init__(self, repo: RepositorioSincronizacion) -> None:
        self._repo = repo

    async def registrar_operacion_en_linea(
        self,
        actor: Actor,
        entidad: str,
        accion: str,
        registro_id: str,
        version: int,
        datos: dict[str, object],
        t_captura: datetime,
    ) -> int:
        raise NoImplementado("Operación en línea")


class ServicioDispositivos:
    """Implementa ``identidad.puertos.PuertoDispositivos``."""

    def __init__(self, repo: RepositorioSincronizacion) -> None:
        self._repo = repo

    async def registrar_o_actualizar(
        self, disp_id: UUID | None, plataforma: str, version_app: str | None, nombre: str | None, usua_id: int
    ) -> UUID:
        """Si ``disp_id`` existe y está activo: actualiza versión/nombre. Si está de baja: 403
        ``DISPOSITIVO_INACTIVO``. Si no viene: crea uno nuevo. Política de dispositivo compartido
        entre usuarios: decisión abierta n.º 4 de la BD v8 (no se inventa).

        Un ``disp_id`` desconocido (p. ej. de otra instalación) o de otra plataforma no se
        reutiliza: se registra un dispositivo nuevo."""
        if disp_id is not None:
            actual = await self._repo.dispositivo(disp_id)
            if actual is not None and not actual["disp_activo"]:
                raise Prohibido(
                    "Este dispositivo fue dado de baja. Contacte al administrador.",
                    codigo="DISPOSITIVO_INACTIVO",
                )
            if actual is None or actual["disp_plataforma"] != plataforma:
                disp_id = None
        return await self._repo.upsert_dispositivo(disp_id, plataforma, version_app, nombre)
