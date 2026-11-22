"""Casos de uso de identidad. Cada método documenta la regla que debe cumplir."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog

from sigat_api.compartido.errores import (
    Conflicto,
    DemasiadasSolicitudes,
    NoAutenticado,
    NoEncontrado,
    NoImplementado,
    Prohibido,
    ReglaNegocio,
)
from sigat_api.compartido.esquemas import Pagina
from sigat_api.compartido.tipos import Actor, Cliente, Plataforma, RolCodigo
from sigat_api.config import Configuracion
from sigat_api.infra.seguridad import contrasenas
from sigat_api.infra.seguridad.tokens import ServicioTokens, hash_refresco
from sigat_api.modules.identidad import esquemas as e
from sigat_api.modules.identidad.puertos import PuertoArchivos, PuertoAvisos, PuertoDispositivos
from sigat_api.modules.identidad.repositorio import RepositorioIdentidad

log = structlog.get_logger()

MENSAJE_CREDENCIALES = "Usuario o contraseña incorrectos."
# Hash de referencia para gastar el mismo tiempo cuando el usuario no existe
# (así la respuesta no revela qué cuentas existen).
_HASH_SEÑUELO = contrasenas.generar_hash("señuelo-sin-cuenta")


@dataclass(frozen=True, slots=True)
class ResultadoSesion:
    """Lo que la ruta necesita para responder: el cuerpo y, en la web, el refresco para la cookie."""

    respuesta: e.RespuestaLogin
    token_refresco: str
    vida_refresco: timedelta
    cliente: Cliente


def _usuario_sesion(fila: dict[str, object]) -> e.UsuarioSesion:
    nombre = f"{str(fila['pers_nombre']).strip()} {str(fila['pers_apellido']).strip()}".strip()
    partes = [p for p in nombre.split() if p]
    iniciales = "".join(p[0] for p in (partes[:1] + partes[-1:] if len(partes) > 1 else partes)).upper()
    etiqueta = None
    if fila["rol_codigo"] == RolCodigo.PRODUCTOR and fila.get("predio_nombre"):
        extra = int(str(fila.get("predios") or 0)) - 1
        etiqueta = f"{fila['predio_nombre']} · durazno" + (f" (+{extra})" if extra > 0 else "")
    return e.UsuarioSesion(
        id=int(str(fila["usua_id"])),
        nombre=nombre,
        iniciales=iniciales or "?",
        usuario=str(fila["usua_usuario"]),
        correo=str(fila["pers_correo"]) if fila.get("pers_correo") else None,
        rol=RolCodigo(str(fila["rol_codigo"])),
        rol_descripcion=str(fila["rol_descripcion"]),
        predio_etiqueta=etiqueta,
        requiere_cambio_contrasena=bool(fila["usua_requiere_reinicio"]),
    )


class ServicioAutenticacion:
    """Inicio de sesión, renovación, cierre, sesión actual y contraseñas (cuentas locales)."""

    def __init__(
        self,
        repo: RepositorioIdentidad,
        dispositivos: PuertoDispositivos,
        tokens: ServicioTokens,
        cfg: Configuracion,
        avisos: PuertoAvisos | None = None,
    ) -> None:
        self._repo = repo
        self._dispositivos = dispositivos
        self._tokens = tokens
        self._cfg = cfg
        self._avisos = avisos

    # ------------------------------------------------------------ inicio de sesión
    async def iniciar_sesion(self, s: e.SolicitudLogin, ip: str | None) -> ResultadoSesion:
        """Reglas:
        * Mensaje ÚNICO "Usuario o contraseña incorrectos" para usuario inexistente,
          contraseña errada o cuenta inactiva (no revelar cuál).
        * Límite de intentos fallidos por usuario y por IP (``SIGAT_LOGIN_*``) -> 429.
        * Cuenta ``externo``: rechazar con código ``USAR_PROVEEDOR_INSTITUCIONAL``.
        * Rol sin fila hija coherente: rechazar (SEGURIDAD S-01 del móvil).
        * TECNICO con cliente=web: rechazar con ``ROL_SOLO_MOVIL`` (el técnico no tiene vistas web).
        * Registrar/actualizar el dispositivo y emitir tokens con ``disp``.
        * Actualizar ``usua_ultimo_acceso``; si el hash necesita rehash, rehacerlo.
        """
        ahora = datetime.now(UTC)
        fila = await self._repo.buscar_para_login(s.usuario)
        # El límite cuenta por CUENTA (entre con usuario o con correo) y, si no existe, por el texto escrito.
        clave_usuario = f"usua:{fila['usua_id']}" if fila else "u:" + s.usuario.strip().lower()
        claves = [clave_usuario] + ([f"ip:{ip}"] if ip else [])
        desde = ahora - timedelta(minutes=self._cfg.login_ventana_minutos)
        if await self._repo.fallos_recientes(clave_usuario, desde) >= self._cfg.login_max_fallos_usuario or (
            ip and await self._repo.fallos_recientes(f"ip:{ip}", desde) >= self._cfg.login_max_fallos_ip
        ):
            raise DemasiadasSolicitudes(
                f"Demasiados intentos fallidos. Espere {self._cfg.login_ventana_minutos} minutos e intente de nuevo.",
                codigo="LOGIN_BLOQUEADO",
            )

        contrasena = s.contrasena.get_secret_value()

        if fila is not None and fila["usua_proveedor"] == "externo":
            raise Prohibido(
                "Esta cuenta entra con el proveedor de identidad institucional.",
                codigo="USAR_PROVEEDOR_INSTITUCIONAL",
            )
        if fila is not None and fila["usua_contrasena"] is None and fila["usua_requiere_reinicio"]:
            raise Prohibido(
                "Debe restablecer su contraseña antes de entrar. Pida al administrador una contraseña nueva.",
                codigo="REQUIERE_RESTABLECER",
            )

        hash_guardado = str(fila["usua_contrasena"]) if fila and fila["usua_contrasena"] else _HASH_SEÑUELO
        coincide = contrasenas.verificar(contrasena, hash_guardado)
        if fila is None or not coincide or fila["estado"] != "activo":
            await self._repo.registrar_intento(claves, exito=False)
            log.info("login_rechazado", motivo="credenciales", cliente=s.cliente.value)
            raise NoAutenticado(MENSAJE_CREDENCIALES, codigo="CREDENCIALES_INVALIDAS")

        if not fila["rol_coherente"]:
            raise Prohibido(
                "La cuenta no tiene completos los datos de su rol. Contacte al administrador.",
                codigo="CUENTA_INCOMPLETA",
            )
        rol = RolCodigo(str(fila["rol_codigo"]))
        if rol is RolCodigo.TECNICO and s.cliente is Cliente.WEB:
            raise Prohibido("El rol Técnico usa solo la aplicación móvil.", codigo="ROL_SOLO_MOVIL")

        usua_id = int(str(fila["usua_id"]))
        await self._repo.fijar_actor(usua_id)
        disp = s.dispositivo or e.DispositivoEntrada(plataforma=Plataforma.OTRO)
        disp_id = await self._dispositivos.registrar_o_actualizar(
            disp.disp_id, disp.plataforma.value, disp.version_app, disp.nombre, usua_id
        )
        nuevo_hash = (
            contrasenas.generar_hash(contrasena) if contrasenas.requiere_rehash(hash_guardado) else None
        )
        await self._repo.registrar_acceso(usua_id, ahora, nuevo_hash)
        await self._repo.registrar_intento([clave_usuario], exito=True)
        log.info("login_ok", usua_id=usua_id, rol=rol.value, cliente=s.cliente.value)
        return await self._emitir(fila, rol, disp_id, s.cliente, uuid.uuid4(), ahora)

    async def _emitir(
        self,
        fila: dict[str, object],
        rol: RolCodigo,
        disp_id: UUID,
        cliente: Cliente,
        familia: UUID,
        ahora: datetime,
    ) -> ResultadoSesion:
        usua_id = int(str(fila["usua_id"]))
        acceso, vida = self._tokens.emitir_acceso(usua_id, rol, disp_id, cliente)
        refresco, hash_hex = self._tokens.nuevo_refresco()
        vida_refresco = self._tokens.vida_refresco(cliente)
        await self._repo.guardar_sesion_refresco(
            usua_id, disp_id, hash_hex, familia, ahora + vida_refresco, cliente.value
        )
        respuesta = e.RespuestaLogin(
            token=acceso,
            expira_en=vida,
            token_refresco=refresco if cliente is Cliente.MOVIL else None,
            disp_id=disp_id,
            user=_usuario_sesion(fila),
        )
        return ResultadoSesion(respuesta, refresco, vida_refresco, cliente)

    # ------------------------------------------------------------ renovación y cierre
    async def refrescar(self, token_refresco: str) -> ResultadoSesion:
        """Rota el refresco. Reuso detectado -> revoca la familia y responde 401."""
        if not token_refresco:
            raise NoAutenticado("No hay una sesión para renovar.", codigo="REFRESCO_AUSENTE")
        ahora = datetime.now(UTC)
        sesion = await self._repo.consumir_sesion_refresco(hash_refresco(token_refresco), ahora)
        if sesion is None:
            raise NoAutenticado("La sesión no es válida. Inicie sesión de nuevo.", codigo="REFRESCO_INVALIDO")
        if sesion["estado"] == "revocada":
            raise NoAutenticado("La sesión fue cerrada. Inicie sesión de nuevo.", codigo="REFRESCO_REVOCADO")
        if sesion["estado"] == "reutilizada":
            log.warning("refresco_reutilizado", usua_id=sesion["usua_id"])
            raise NoAutenticado(
                "La sesión se cerró por seguridad. Inicie sesión de nuevo.", codigo="REFRESCO_REUTILIZADO"
            )
        if sesion["sesr_expira_en"] <= ahora:
            raise NoAutenticado("Su sesión venció. Inicie sesión de nuevo.", codigo="REFRESCO_VENCIDO")
        fila = await self._repo.usuario_por_id(int(sesion["usua_id"]))
        if fila is None or fila["estado"] != "activo" or not fila["rol_coherente"]:
            raise NoAutenticado("La cuenta ya no está activa.", codigo="CUENTA_INACTIVA")
        await self._repo.fijar_actor(int(sesion["usua_id"]))
        return await self._emitir(
            fila,
            RolCodigo(str(fila["rol_codigo"])),
            sesion["disp_id"],
            Cliente(sesion["sesr_cliente"]),
            sesion["sesr_familia"],
            ahora,
        )

    async def cerrar_sesion(self, actor: Actor) -> None:
        """Revoca el refresco del dispositivo actual. El móvil además borra su base local cifrada."""
        await self._repo.revocar_sesiones(actor.usua_id, datetime.now(UTC), actor.disp_id)

    async def sesion_actual(self, actor: Actor) -> e.UsuarioSesion:
        fila = await self._repo.usuario_por_id(actor.usua_id)
        if fila is None or fila["estado"] != "activo":
            raise NoAutenticado("La cuenta ya no está activa.", codigo="CUENTA_INACTIVA")
        return _usuario_sesion(fila)

    # ------------------------------------------------------------ contraseñas
    async def solicitar_recuperacion(self, s: e.SolicitudRecuperacion) -> None:
        """«¿Problemas para ingresar?». Responde igual exista o no la cuenta (no enumera cuentas).

        Si la cuenta es local y está activa:
        1. Guarda la solicitud en ``restablecimiento_clave`` (solo el hash de un token, con vencimiento).
        2. Crea un aviso de categoría ``solicitudes_cuenta`` para CADA administrador activo, que abre
           Usuarios en la web y asigna una contraseña nueva (``ServicioUsuarios.asignar_contrasena``);
           el administrador se la entrega a la persona por el medio que elija.
        Si ya hay una solicitud sin atender de los últimos ``SIGAT_RECUPERACION_MINUTOS``, no repite avisos.
        """
        fila = await self._repo.buscar_para_login(s.identificador)
        if fila is None or fila["usua_proveedor"] != "local" or fila["estado"] != "activo":
            log.info("recuperacion_sin_cuenta")
            return
        usua_id = int(str(fila["usua_id"]))
        ahora = datetime.now(UTC)
        ventana = timedelta(minutes=self._cfg.recuperacion_minutos)
        await self._repo.fijar_actor(usua_id)
        if await self._repo.solicitud_reciente(usua_id, ahora - ventana):
            log.info("recuperacion_repetida", usua_id=usua_id)
            return
        _token, hash_hex = self._tokens.nuevo_refresco()  # el token en claro no se guarda ni se envía
        rest_id = await self._repo.crear_restablecimiento(usua_id, hash_hex, ahora + ventana)
        admins = await self._repo.administradores_activos(excepto=None)
        if self._avisos is not None:
            nombre = f"{fila['pers_nombre']} {fila['pers_apellido']}".strip()
            for admin_id in admins:
                await self._avisos.emitir(
                    admin_id,
                    "solicitudes_cuenta",
                    "Solicitud de nueva contraseña",
                    f"{nombre} ({fila['usua_usuario']}, {fila['rol_descripcion']}) pidió ayuda para ingresar. "
                    "Asígnele una contraseña nueva desde Usuarios y entréguesela.",
                    f"restablecimiento:{rest_id}",
                    "/admin/usuarios",
                    str(usua_id),
                )
        log.info("recuperacion_solicitada", usua_id=usua_id, administradores_avisados=len(admins))

    async def confirmar_recuperacion(self, s: e.ConfirmacionRecuperacion) -> None:
        """Token de uso único y con vencimiento. Revoca todas las sesiones del usuario."""
        ahora = datetime.now(UTC)
        usua_id = await self._repo.usar_restablecimiento(hash_refresco(s.token), ahora)
        if usua_id is None:
            raise ReglaNegocio("El enlace no es válido o ya venció.", codigo="RESTABLECIMIENTO_INVALIDO")
        await self._repo.fijar_actor(usua_id)
        await self._guardar_contrasena(usua_id, s.nueva_contrasena.get_secret_value())
        await self._repo.revocar_sesiones(usua_id, ahora)

    async def cambiar_contrasena(self, actor: Actor, s: e.CambioContrasena) -> None:
        """Exige la contraseña actual. Cierra las sesiones de los demás dispositivos."""
        fila = await self._repo.usuario_por_id(actor.usua_id)
        if fila is None or not fila["usua_contrasena"]:
            raise NoAutenticado("La cuenta ya no está activa.", codigo="CUENTA_INACTIVA")
        if not contrasenas.verificar(s.actual.get_secret_value(), str(fila["usua_contrasena"])):
            raise ReglaNegocio("La contraseña actual no es correcta.", codigo="CONTRASENA_ACTUAL_INCORRECTA")
        nueva = s.nueva.get_secret_value()
        if nueva.strip().lower() in {
            str(fila["usua_usuario"]).lower(),
            str(fila["pers_correo"] or "").lower(),
        }:
            raise ReglaNegocio(
                "La contraseña no puede ser igual al usuario ni al correo.", codigo="CONTRASENA_DEBIL"
            )
        await self._guardar_contrasena(actor.usua_id, nueva)
        ahora = datetime.now(UTC)
        await self._repo.revocar_sesiones(actor.usua_id, ahora, excepto_disp=actor.disp_id)

    async def _guardar_contrasena(self, usua_id: int, contrasena: str) -> None:
        if len(contrasena) < 10:
            raise ReglaNegocio("La contraseña debe tener al menos 10 caracteres.", codigo="CONTRASENA_DEBIL")
        await self._repo.actualizar_contrasena(usua_id, contrasenas.generar_hash(contrasena))


class ServicioPerfil:
    def __init__(self, repo: RepositorioIdentidad, archivos: PuertoArchivos) -> None:
        self._repo = repo
        self._archivos = archivos

    async def perfil(self, actor: Actor) -> e.PerfilPropio:
        f = await self._repo.perfil(actor.usua_id)
        if f is None:
            raise NoAutenticado("La cuenta ya no está activa.", codigo="CUENTA_INACTIVA")
        return e.PerfilPropio(
            usua_id=int(f["usua_id"]),
            nombre=str(f["nombre"]),
            usuario=str(f["usua_usuario"]),
            correo=f["pers_correo"],
            telefono=f["pers_telefono"],
            rol=RolCodigo(str(f["rol_codigo"])),
            rol_descripcion=str(f["rol_descripcion"]),
            foto_arch_id=f["arch_id_foto"],
            foto_url=None,
            actualizado_en=f["perf_actualizado_en"],
        )

    async def poner_foto(self, actor: Actor, s: e.FotoPerfilEntrada) -> e.PerfilPropio:
        """El archivo debe ser ``foto_perfil``, del mismo usuario y ``almacenado`` (validado por contenido)."""
        raise NoImplementado("Foto de perfil")

    async def quitar_foto(self, actor: Actor) -> e.PerfilPropio:
        raise NoImplementado("Quitar foto de perfil")

    async def predios(self, actor: Actor) -> list[e.PredioAccesible]:
        filas = await self._repo.predios_accesibles(actor.usua_id, actor.rol is RolCodigo.ADMINISTRADOR)
        return [
            e.PredioAccesible(
                pred_id=int(f["pred_id"]), pred_nombre=str(f["pred_nombre"]), origen=f["origen"]
            )
            for f in filas
        ]


def _usuario_admin(f: dict[str, object]) -> e.UsuarioAdmin:
    nombre, apellido = str(f["pers_nombre"]).strip(), str(f["pers_apellido"]).strip()
    rol = RolCodigo(str(f["rol_codigo"]))
    return e.UsuarioAdmin(
        usua_id=int(str(f["usua_id"])),
        pers_cc=int(str(f["pers_cc"])),
        nombre=nombre,
        apellido=apellido,
        nombre_completo=f"{nombre} {apellido}".strip(),
        iniciales=(nombre[:1] + apellido[:1]).upper() or "?",
        correo=f["pers_correo"],
        telefono=f["pers_telefono"],
        direccion=f["pers_direccion"],
        usuario=str(f["usua_usuario"]),
        rol=rol,
        rol_descripcion=str(f["rol_descripcion"]),
        acceso="solo_movil" if rol is RolCodigo.TECNICO else "web_y_movil",
        activo=bool(f["activo"]),
        proveedor=f["usua_proveedor"],
        ultimo_acceso=f["usua_ultimo_acceso"],
        requiere_cambio_contrasena=bool(f["usua_requiere_reinicio"]),
        cent_id=f["cent_id"],
        tecn_codigo=f["tecn_codigo"],
        muni_id=f["muni_id"],
        predios=int(str(f["predios_vigentes"] or 0)),
    )


TAMANO_PAGINA_USUARIOS = 200


class ServicioUsuarios:
    """Solo ADMINISTRADOR (``usuarios.administrar``, lo exige la ruta). Cada escritura queda en la
    bitácora con el administrador como autor (``sigat.usuario_actual`` = actor, fn_auditar)."""

    def __init__(self, repo: RepositorioIdentidad) -> None:
        self._repo = repo

    async def roles(self) -> list[e.Rol]:
        return [e.Rol(**f) for f in await self._repo.roles()]

    async def listar(
        self, actor: Actor, rol: RolCodigo | None, activo: bool | None, cursor: str | None
    ) -> Pagina[e.UsuarioAdmin]:
        """``v_usuario_admin`` (excluye eliminados), por ``usua_id``."""
        despues = int(cursor) if cursor and cursor.isdigit() else None
        filas = await self._repo.listar_usuarios(
            rol.value if rol else None, activo, despues, TAMANO_PAGINA_USUARIOS + 1
        )
        items = [_usuario_admin(f) for f in filas[:TAMANO_PAGINA_USUARIOS]]
        siguiente = str(items[-1].usua_id) if len(filas) > TAMANO_PAGINA_USUARIOS else None
        return Pagina[e.UsuarioAdmin](items=items, siguiente=siguiente)

    async def obtener(self, actor: Actor, usua_id: int) -> e.UsuarioAdmin:
        f = await self._repo.usuario_admin(usua_id)
        if f is None:
            raise NoEncontrado("El usuario no existe o fue eliminado.", codigo="USUARIO_NO_ENCONTRADO")
        return _usuario_admin(f)

    async def _vigente(self, usua_id: int) -> dict[str, object]:
        f = await self._repo.estado_cuenta(usua_id)
        if f is None:
            raise NoEncontrado("El usuario no existe.", codigo="USUARIO_NO_ENCONTRADO")
        if f["usua_eliminado_en"] is not None:
            raise Conflicto("El usuario fue dado de baja y ya no se modifica.", codigo="USUARIO_ELIMINADO")
        return f

    async def crear(self, actor: Actor, s: e.NuevoUsuario) -> e.UsuarioAdmin:
        """En UNA transacción: persona -> fila de rol -> usuario -> perfil (técnico: cent_id y el
        administrador que lo crea; productor: municipio y ``prod_usua_registro_id`` = actor; agrónomo:
        ninguna). Usuario y correo únicos sin distinguir mayúsculas -> 409 ``USUARIO_DUPLICADO`` /
        ``CORREO_DUPLICADO``; cédula existente -> 409 ``CEDULA_DUPLICADA``.
        Sin ``contrasena_temporal`` la cuenta no puede entrar hasta que el administrador le asigne una."""
        if s.proveedor == "externo":
            raise ReglaNegocio(
                "Las cuentas del proveedor institucional aún no están habilitadas (RI-008).",
                codigo="PROVEEDOR_EXTERNO_PENDIENTE",
            )
        clave = s.contrasena_temporal.get_secret_value() if s.contrasena_temporal else None
        if clave is not None:
            _exigir_contrasena_valida(clave, s.usuario, str(s.correo or ""))
        usua_id = await self._repo.crear_usuario(
            {
                "pers_cc": s.pers_cc,
                "nombre": s.nombre.strip(),
                "apellido": s.apellido.strip(),
                "correo": str(s.correo) if s.correo else None,
                "telefono": s.telefono,
                "direccion": s.direccion,
                "usuario": s.usuario.strip(),
                "hash_contrasena": contrasenas.generar_hash(clave) if clave else None,
                "requiere_reinicio": clave is not None,  # la contraseña inicial se cambia al entrar
                "rol": s.rol.value,
                "cent_id": s.datos_rol.cent_id,
                "tecn_codigo": s.datos_rol.tecn_codigo,
                "muni_id": s.datos_rol.muni_id,
                "registrado_por": actor.usua_id,
            }
        )
        log.info("usuario_creado", usua_id=usua_id, rol=s.rol.value, por=actor.usua_id)
        return await self.obtener(actor, usua_id)

    async def actualizar(self, actor: Actor, usua_id: int, s: e.ActualizacionUsuario) -> e.UsuarioAdmin:
        """Editar un usuario (nombre mal escrito, correo, teléfono, usuario, rol, cédula). En UNA
        transacción. Cambiar el rol revoca sus sesiones; la fila del rol anterior no se borra.
        ``pers_cc`` usa ``fn_corregir_cedula`` (si hay registros a nombre de la persona, la BD lo
        impide y responde con su mensaje)."""
        cuenta = await self._vigente(usua_id)
        campos = s.model_fields_set
        if (
            "rol" in campos
            and s.rol is not None
            and usua_id == actor.usua_id
            and s.rol is not RolCodigo.ADMINISTRADOR
        ):
            raise Conflicto("Un administrador no puede quitarse su propio rol.", codigo="AUTO_DEGRADACION")
        if (
            "rol" in campos
            and cuenta["rol_codigo"] == "ADMINISTRADOR"
            and s.rol is not RolCodigo.ADMINISTRADOR
            and not await self._repo.administradores_activos(excepto=usua_id)
        ):
            raise Conflicto(
                "No se puede dejar el sistema sin administradores activos.", codigo="ULTIMO_ADMINISTRADOR"
            )
        persona = {
            columna: valor
            for campo, columna, valor in (
                ("nombre", "pers_nombre", s.nombre.strip() if s.nombre else None),
                ("apellido", "pers_apellido", s.apellido.strip() if s.apellido else None),
                ("correo", "pers_correo", str(s.correo) if s.correo else None),
                ("telefono", "pers_telefono", s.telefono or None),
                ("direccion", "pers_direccion", s.direccion or None),
            )
            if campo in campos
        }
        usuario: dict[str, object] = {}
        if "usuario" in campos and s.usuario:
            usuario["usua_usuario"] = s.usuario.strip()
        if "rol" in campos and s.rol is not None and s.rol.value != cuenta["rol_codigo"]:
            usuario["rol"] = s.rol.value
            datos = s.datos_rol or e.DatosRolEntrada()
            usuario["datos_rol"] = {**datos.model_dump(), "registrado_por": actor.usua_id}
        await self._repo.actualizar_usuario(usua_id, persona, usuario)
        if "pers_cc" in campos and s.pers_cc is not None and s.pers_cc != cuenta["pers_cc"]:
            await self._repo.corregir_cedula(usua_id, s.pers_cc)
        if "rol" in usuario:
            await self._repo.revocar_sesiones(usua_id, datetime.now(UTC))
        log.info("usuario_actualizado", usua_id=usua_id, campos=sorted(campos), por=actor.usua_id)
        return await self.obtener(actor, usua_id)

    async def activar(self, actor: Actor, usua_id: int, s: e.ActivacionUsuario) -> e.UsuarioAdmin:
        """Activar/desactivar (reversible, distinto de eliminar). Al desactivar se cierran sus
        sesiones. Un administrador no se desactiva a sí mismo ni deja el sistema sin administradores."""
        cuenta = await self._vigente(usua_id)
        if not s.activo:
            if usua_id == actor.usua_id:
                raise Conflicto("No puede desactivar su propia cuenta.", codigo="AUTO_DESACTIVACION")
            if cuenta["rol_codigo"] == "ADMINISTRADOR" and not await self._repo.administradores_activos(
                excepto=usua_id
            ):
                raise Conflicto(
                    "No se puede dejar el sistema sin administradores activos.", codigo="ULTIMO_ADMINISTRADOR"
                )
        await self._repo.cambiar_estado(usua_id, s.activo)
        if not s.activo:
            await self._repo.revocar_sesiones(usua_id, datetime.now(UTC))
        log.info("usuario_activacion", usua_id=usua_id, activo=s.activo, por=actor.usua_id)
        return await self.obtener(actor, usua_id)

    async def vista_previa_eliminacion(self, actor: Actor, usua_id: int) -> e.VistaPreviaEliminacion:
        """``fn_usuario_tiene_actividad``: el texto del diálogo antes de confirmar."""
        await self._vigente(usua_id)
        actividad = await self._repo.tiene_actividad(usua_id)
        return e.VistaPreviaEliminacion(
            usua_id=usua_id,
            tiene_actividad=actividad,
            resultado_esperado="dado_de_baja" if actividad else "eliminado",
            explicacion=(
                "Tiene registros en el sistema: se dará de baja definitiva y su historia se conserva."
                if actividad
                else "No tiene registros: se borrará por completo."
            ),
        )

    async def eliminar(self, actor: Actor, usua_id: int, s: e.EliminacionUsuario) -> e.ResultadoEliminacion:
        """``fn_eliminar_usuario`` decide: sin actividad -> borrado físico; con actividad -> baja
        definitiva. A sí mismo o al último administrador -> la BD lo impide (409)."""
        await self._vigente(usua_id)
        if usua_id == actor.usua_id:
            raise Conflicto("No puede eliminar su propia cuenta.", codigo="AUTO_ELIMINACION")
        await self._repo.revocar_sesiones(usua_id, datetime.now(UTC))
        resultado = await self._repo.eliminar_usuario(usua_id, s.motivo.strip())
        log.info("usuario_eliminado", usua_id=usua_id, resultado=resultado, por=actor.usua_id)
        return e.ResultadoEliminacion(
            usua_id=usua_id,
            resultado="dado_de_baja" if resultado == "dado_de_baja" else "eliminado",
            mensaje="Usuario dado de baja; su historia se conserva."
            if resultado == "dado_de_baja"
            else "Usuario eliminado.",
        )

    async def asignar_contrasena(self, actor: Actor, usua_id: int, s: e.AsignacionContrasena) -> None:
        """El administrador asigna una contraseña nueva (p. ej. tras un aviso «Solicitud de nueva
        contraseña»). Cierra todas las sesiones de esa persona y marca atendidas sus solicitudes.
        Nunca se devuelve ni se envía la contraseña: el administrador la entrega por su cuenta."""
        cuenta = await self._vigente(usua_id)
        if cuenta["usua_proveedor"] != "local":
            raise ReglaNegocio(
                "Esta cuenta entra con el proveedor institucional; no tiene contraseña en SIGAT.",
                codigo="CUENTA_EXTERNA",
            )
        clave = s.nueva_contrasena.get_secret_value()
        f = await self._repo.usuario_por_id(usua_id)
        _exigir_contrasena_valida(
            clave, str(f["usua_usuario"]) if f else "", str(f["pers_correo"] or "") if f else ""
        )
        ahora = datetime.now(UTC)
        await self._repo.asignar_contrasena(usua_id, contrasenas.generar_hash(clave), s.exigir_cambio, ahora)
        await self._repo.revocar_sesiones(usua_id, ahora)
        log.info("contrasena_asignada", usua_id=usua_id, por=actor.usua_id)

    async def asignaciones(self, actor: Actor, usua_id: int) -> list[e.AsignacionPredio]:
        raise NoImplementado("Asignaciones de predio")

    async def asignar_predio(
        self, actor: Actor, usua_id: int, s: e.NuevaAsignacionPredio
    ) -> e.AsignacionPredio:
        """La BD impide solapes (EXCLUDE usuario_predio_sin_solape) -> 409."""
        raise NoImplementado("Asignación de predio")

    async def cerrar_asignacion(self, actor: Actor, usua_id: int, usup_id: int) -> None:
        """No borra: fija ``usup_hasta = hoy``."""
        raise NoImplementado("Cierre de asignación")


def _exigir_contrasena_valida(clave: str, usuario: str, correo: str) -> None:
    """Política mínima (confirmar con el responsable de seguridad): 10+ caracteres y distinta del usuario y del correo."""
    if len(clave) < 10:
        raise ReglaNegocio("La contraseña debe tener al menos 10 caracteres.", codigo="CONTRASENA_DEBIL")
    if clave.strip().lower() in {usuario.strip().lower(), correo.strip().lower()} - {""}:
        raise ReglaNegocio(
            "La contraseña no puede ser igual al usuario ni al correo.", codigo="CONTRASENA_DEBIL"
        )


class ServicioAutorizacion:
    """Comprobaciones de alcance (además del rol)."""

    def __init__(self, repo: RepositorioIdentidad) -> None:
        self._repo = repo

    async def exigir_lote_visible(self, actor: Actor, lote_id: int) -> None:
        """``lote_id IN (SELECT * FROM fn_lotes_visibles(:usua))``; si no -> 404 (no 403, para no revelar)."""
        raise NoImplementado("Visibilidad de lote")

    async def exigir_predio_visible(self, actor: Actor, pred_id: int) -> None:
        raise NoImplementado("Visibilidad de predio")

    async def exigir_disp_del_actor(self, actor: Actor, disp_id: UUID) -> None:
        """El ``disp_id`` del cuerpo del sync debe ser el del token."""
        raise NoImplementado("Dispositivo del actor")
