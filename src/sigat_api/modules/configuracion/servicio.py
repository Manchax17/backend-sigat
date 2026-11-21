"""Casos de uso de configuración."""

import hashlib
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import rfc8785

from sigat_api.compartido.errores import Conflicto, NoEncontrado, NoImplementado, ReglaNegocio
from sigat_api.compartido.tipos import Actor
from sigat_api.infra.cola.cola import Cola
from sigat_api.modules.configuracion import esquemas as e
from sigat_api.modules.configuracion.dominio import error_codigo_trampa
from sigat_api.modules.configuracion.repositorio import RepositorioConfiguracion

_TZ_COLOMBIA = ZoneInfo("America/Bogota")

# Columnas auxiliares que trae la vista para saber por qué no se puede confirmar la posición.
# No forman parte del contrato HTTP: se quitan antes de construir el esquema de trampa.
_AUXILIARES_TRAMPA = frozenset({"lote_sin_geometria", "con_ubicacion_vigente"})


def _trampa(fila: dict[str, Any]) -> e.Trampa:
    """Construye el esquema de trampa a partir de una fila de la vista de la aplicación."""
    return e.Trampa(**{k: v for k, v in fila.items() if k not in _AUXILIARES_TRAMPA})


class ServicioPredios:
    def __init__(self, repo: RepositorioConfiguracion, cola: Cola) -> None:
        self._repo = repo
        self._cola = cola

    async def listar(self, actor: Actor) -> list[e.Predio]:
        filas = await self._repo.predios_visibles(actor.usua_id)
        return [e.Predio(**f) for f in filas]

    async def obtener(self, actor: Actor, pred_id: int) -> e.Predio:
        visible = await self._repo.predio_visible(actor.usua_id, pred_id)
        if not visible:
            raise NoEncontrado("El predio no existe o no tiene acceso a él.", codigo="PREDIO_NO_ENCONTRADO")
        fila = await self._repo.predio(pred_id)
        if not fila:
            raise NoEncontrado("El predio no existe.", codigo="PREDIO_NO_ENCONTRADO")
        return e.Predio(**fila)

    async def actualizar(self, actor: Actor, pred_id: int, s: e.ActualizacionPredio) -> e.Predio:
        """Cartografía del admin (web ``updateFarm``). Invalida el paquete de campo."""
        raise NoImplementado("Actualizar predio")

    async def poner_geometria(self, actor: Actor, pred_id: int, s: e.EntradaGeometria) -> e.Predio:
        """Valida SRID 4326, ST_IsValid y que contenga a los lotes vigentes. Invalida el paquete."""
        raise NoImplementado("Geometría del predio")

    async def mapa(self, actor: Actor, pred_id: int, instante: datetime | None) -> e.MapaPredio:
        """``instante`` permite ver el contorno vigente en una fecha pasada (ADR-06)."""
        raise NoImplementado("Mapa del predio")


class ServicioLotes:
    def __init__(self, repo: RepositorioConfiguracion, cola: Cola) -> None:
        self._repo = repo
        self._cola = cola

    async def listar(self, actor: Actor, pred_id: int | None, incluir_indicadores: bool) -> list[e.Lote]:
        """Lotes visibles para el actor. La visibilidad y la asignación las resuelve
        ``fn_lotes_visibles`` (a través de ``fn_lote_app``): administrador todos; productor y
        agrónomo/supervisor los de sus predios (``v_acceso_predio``); técnico los de sus
        asignaciones vigentes. ``pred_id`` solo acota el resultado. Los lotes dados de baja no
        aparecen (``v_lote_app``).

        ``incluir_indicadores`` es de la web (tarjetas del panel de administración). Los indicadores
        se calculan con las visitas, el programa y los periodos de trampeo, que son de otro sprint
        (fuera del alcance del issue #6); mientras tanto el bloque va en ``null`` (lo admite el
        esquema) en vez de inventar cifras. La BD aún no tiene vista ni función que calcule los
        campos obligatorios de ``IndicadoresLote``: ver CHANGELOG.md, «Pendientes técnicos».
        """
        filas = await self._repo.lotes_visibles(actor.usua_id, pred_id)
        return [e.Lote(**fila) for fila in filas]

    async def obtener(self, actor: Actor, lote_id: int) -> e.LoteDetalle:
        """Detalle del lote. Si no es visible para el actor -> 404 (no 403: no se revela que el
        lote existe pero pertenece a otro usuario)."""
        if not await self._repo.lote_visible(actor.usua_id, lote_id):
            raise NoEncontrado("El lote no existe o no es visible para usted.", codigo="LOTE_NO_ENCONTRADO")
        fila = await self._repo.lote(lote_id)
        if fila is None:
            raise NoEncontrado("El lote no existe o no es visible para usted.", codigo="LOTE_NO_ENCONTRADO")
        return e.LoteDetalle(**fila)

    async def crear(self, actor: Actor, pred_id: int, s: e.NuevoLote) -> e.LoteDetalle:
        """(pred_id, lote_codigo) único. Si trae geometría, crea la versión 1."""
        raise NoImplementado("Alta de lote")

    async def actualizar(self, actor: Actor, lote_id: int, s: e.ActualizacionLote) -> e.LoteDetalle:
        """Actualiza solo los campos permitidos. El productor no se edita aquí (sale del predio).

        La autoría queda en ``bitacora_auditoria`` (``trg_aud_lote`` + ``sigat.usuario_actual``).
        ``lote_tecnico_actualizacion_id`` NO se llena: es FK a ``tecnico`` y los roles que editan
        lotes (administrador, agrónomo/supervisor) no tienen fila ahí.
        """
        if not await self._repo.lote_visible(actor.usua_id, lote_id):
            raise NoEncontrado("El lote no existe o no es visible para usted.", codigo="LOTE_NO_ENCONTRADO")
        cambios = s.model_dump(exclude_unset=True)
        if not cambios:
            raise ReglaNegocio("Debe enviar al menos un campo para actualizar.", codigo="ACTUALIZACION_VACIA")

        # Columnas NOT NULL: un null explícito no es "no cambiar", es un error.
        for campo in ("lote_descripcion", "lote_area"):
            if campo in cambios and cambios[campo] is None:
                raise ReglaNegocio(f"{campo} no puede ser nulo.", codigo="CAMPO_OBLIGATORIO")

        # Coherencia especie/variedad antes de llegar a la FK compuesta.
        actual = None
        if "lote_fecha_baja" in cambios or ("espc_id" in cambios) != ("vari_id" in cambios):
            actual = await self._repo.lote(lote_id)  # v_lote_app; None si no existe o ya está de baja
            if actual is None:
                raise NoEncontrado(
                    "El lote no existe o no es visible para usted.", codigo="LOTE_NO_ENCONTRADO"
                )
            espc = cambios.get("espc_id", actual["espc_id"])
            vari = cambios.get("vari_id", actual["vari_id"])
            if vari is not None and espc is None:
                raise ReglaNegocio("No se puede asignar variedad sin especie.", codigo="VARIEDAD_SIN_ESPECIE")
            if "espc_id" in cambios and espc != actual["espc_id"] and actual["vari_id"] is not None:
                raise ReglaNegocio(
                    "Al cambiar la especie debe enviar también vari_id (o null para limpiarla).",
                    codigo="VARIEDAD_INCOHERENTE",
                )

        cambios["lote_fecha_actualizacion"] = datetime.now(_TZ_COLOMBIA).date()

        await self._repo.actualizar_lote(lote_id, cambios)

        fila = await self._repo.lote(lote_id)
        if fila is None and actual is not None:
            # Este PATCH dio de baja el lote: v_lote_app ya lo oculta. Se responde con la fila
            # previa más los campos que acaban de cambiar.
            fila = {**actual, **{k: v for k, v in cambios.items() if k in actual}}
        if fila is None:
            raise NoEncontrado("El lote no existe o no es visible para usted.", codigo="LOTE_NO_ENCONTRADO")
        return e.LoteDetalle(**fila)

    async def geometrias(self, actor: Actor, lote_id: int) -> list[e.VersionGeometriaLote]:
        """Versiones de la geometría del lote (ADR-06)."""
        raise NoImplementado("Versiones de geometría")

    async def nueva_geometria(
        self, actor: Actor, lote_id: int, s: e.EntradaGeometria
    ) -> e.VersionGeometriaLote:
        """ADR-06: genera evento de cambio de área (puede afectar la normalización del MTD) e invalida paquete."""
        raise NoImplementado("Nueva geometría de lote")

    async def contar_sobre_umbral(self, actor: Actor) -> int:
        """Total de lotes visibles que superan el umbral para este agronomo"""
        return await self._repo.contar_lotes_sobre_umbral(actor.usua_id)


class ServicioTrampas:
    def __init__(self, repo: RepositorioConfiguracion, cola: Cola) -> None:
        self._repo = repo
        self._cola = cola

    async def listar(self, actor: Actor, lote_id: int | None, pred_id: int | None) -> list[e.Trampa]:
        """Trampas visibles para el actor. El permiso lo resuelve ``fn_trampa_app`` (a través de
        ``fn_lotes_visibles``); ``lote_id`` y ``pred_id`` solo acotan el resultado."""
        filas = await self._repo.trampas_visibles(actor.usua_id, lote_id, pred_id)
        return [_trampa(fila) for fila in filas]

    async def obtener(self, actor: Actor, tram_id: int) -> e.Trampa:
        """Detalle de la trampa. Si no es visible para el actor -> 404 (no 403)."""
        fila = await self._repo.trampa_por_id(actor.usua_id, tram_id)
        if fila is None:
            raise NoEncontrado(
                "La trampa no existe o no es visible para usted.", codigo="TRAMPA_NO_ENCONTRADA"
            )
        return _trampa(fila)

    async def buscar_por_codigo(
        self,
        actor: Actor,
        tram_codigo: str,
        lote_activo: int | None,
        lat: Decimal | None,
        lng: Decimal | None,
    ) -> e.BusquedaTrampa:
        """RF-012: compara trampa–lote–ubicación. ADVIERTE, no bloquea. Código inexistente ->
        200 con ``en_catalogo=false`` (no 404), para que el móvil muestre su pantalla de validación.

        La búsqueda es sobre todo el catálogo: el técnico tiene que poder validar la trampa que
        tiene delante aunque no sea la que tiene asignada (por eso las advertencias existen).
        """
        fila = await self._repo.trampa_por_codigo(tram_codigo)
        if fila is None:
            return e.BusquedaTrampa(
                trampa=None,
                en_catalogo=False,
                pertenece_lote_activo=None,
                dentro_del_lote=None,
                advertencias=[f"El código {tram_codigo} no está en el catálogo de trampas."],
            )
        if not fila.get("con_ubicacion_vigente"):
            return e.BusquedaTrampa(
                trampa=None,
                en_catalogo=True,
                pertenece_lote_activo=None,
                dentro_del_lote=None,
                advertencias=[f"La trampa {tram_codigo} no tiene ubicación vigente: no se puede validar."],
            )

        trampa = _trampa(fila)
        advertencias: list[str] = []

        pertenece = None if lote_activo is None else trampa.lote_id == lote_activo
        if pertenece is False:
            advertencias.append("La trampa no pertenece al lote que tiene abierto.")

        if fila.get("lote_sin_geometria"):
            advertencias.append(
                "El lote no tiene geometría registrada: no se puede comprobar la posición de la trampa."
            )
        elif fila.get("dentro_del_lote") is False:
            advertencias.append("La trampa está fuera del lote al que está asignada.")

        dentro_del_lote: bool | None = None
        if lat is not None and lng is not None:
            lote_del_punto = await self._repo.lote_que_contiene(float(lng), float(lat), datetime.now(UTC))
            if lote_activo is not None:
                dentro_del_lote = lote_del_punto == lote_activo
            else:
                dentro_del_lote = lote_del_punto is not None
            if not dentro_del_lote:
                advertencias.append("Su ubicación no está dentro del lote que tiene abierto.")

        return e.BusquedaTrampa(
            trampa=trampa,
            en_catalogo=True,
            pertenece_lote_activo=pertenece,
            dentro_del_lote=dentro_del_lote,
            advertencias=advertencias,
        )

    async def crear(self, actor: Actor, s: e.NuevaTrampa) -> e.Trampa:
        """Reglas: ``dominio.error_codigo_trampa`` (formato y paridad RN-005, mismos textos que
        ``sigat.fn_codigo_trampa_error``) -> 422 ``CODIGO_TRAMPA_INVALIDO``; código existente -> 409
        ``CODIGO_TRAMPA_DUPLICADO``; la ubicación debe caer en el lote (advertencia) y respetar la
        distancia mínima RN-004 con las vigentes.

        Que el punto caiga dentro del lote NO bloquea (RF-012): la respuesta lo informa en
        ``dentro_del_lote``. La trampa nace activa y a nombre del técnico de la ubicación inicial.
        """
        u = s.ubicacion
        pred_id = await self._predio_destino(actor, u)

        tipo = await self._repo.tipo_trampa(s.tipt_id)
        if tipo is None:
            raise ReglaNegocio("El tipo de trampa no existe.", codigo="TIPO_TRAMPA_INVALIDO")
        error = error_codigo_trampa(s.tram_codigo, tipo["tipt_paridad"])
        if error is not None:
            raise ReglaNegocio(error, codigo="CODIGO_TRAMPA_INVALIDO")
        if await self._repo.existe_codigo_trampa(s.tram_codigo):
            raise Conflicto(
                f"Duplicidad de código de trampa: {s.tram_codigo}", codigo="CODIGO_TRAMPA_DUPLICADO"
            )
        await self._exigir_distancia_minima(pred_id, u, excluir_tram_id=None)

        tram_id = await self._repo.insertar_trampa(
            {
                "tram_codigo": s.tram_codigo,
                "tipt_id": s.tipt_id,
                "atra_id": s.atra_id,
                "tram_tecnico_registro_id": u.tecn_id,
            },
            {**u.model_dump(), "pred_id": pred_id},
        )
        return await self.obtener(actor, tram_id)

    async def _predio_destino(self, actor: Actor, u: e.NuevaUbicacion) -> int:
        """Predio del lote donde se instala la trampa.

        Lote ajeno o inexistente -> 404 (no 403). El lote debe estar activo en la fecha de
        instalación (tabla ``lote``: activo entre ``lote_fecha_registro`` y ``lote_fecha_baja``)
        -> si no, 422 ``LOTE_INACTIVO``.
        """
        pred_id = (
            await self._repo.predio_del_lote(u.lote_id)
            if await self._repo.lote_visible(actor.usua_id, u.lote_id)
            else None
        )
        if pred_id is None:
            raise NoEncontrado("El lote no existe o no es visible para usted.", codigo="LOTE_NO_ENCONTRADO")
        if not await self._repo.lote_activo_en(u.lote_id, u.ubic_fecha):
            raise ReglaNegocio(
                "El lote no está activo en la fecha de instalación: aún no estaba registrado o fue dado de baja.",
                codigo="LOTE_INACTIVO",
            )
        return pred_id

    async def _exigir_distancia_minima(
        self, pred_id: int, u: e.NuevaUbicacion, excluir_tram_id: int | None
    ) -> None:
        """RN-004: ninguna trampa vigente del predio a menos de la distancia del programa vigente.

        Sin programa vigente en la fecha de instalación no hay distancia que exigir: se omite la
        comprobación en vez de inventar un valor (el rango 40-60 m lo fija cada programa).
        """
        distancia = await self._repo.distancia_minima_trampas(pred_id, u.ubic_fecha)
        if distancia is None:
            return
        cercanas = await self._repo.trampas_cercanas(
            pred_id, u.ubic_long, u.ubic_lat, u.ubic_fecha, distancia, excluir_tram_id
        )
        if cercanas:
            raise ReglaNegocio(
                f"La trampa queda a menos de {distancia} m de: {', '.join(cercanas)} (RN-004).",
                codigo="DISTANCIA_MINIMA_TRAMPAS",
            )

    async def actualizar(self, actor: Actor, tram_id: int, s: e.ActualizacionTrampa) -> e.Trampa:
        """Cambia atrayente y estado (activa/inactiva). Código y tipo no se editan (RN-005).

        Cambiar ``esta_id`` exige ``tecn_id_estado``, el técnico responsable del cambio (contrato)
        -> 422 ``TECNICO_ESTADO_REQUERIDO``. Cuando viene, también queda como técnico de la última
        actualización; si no viene (p. ej. solo cambia el atrayente) ese campo queda nulo, porque el
        contrato no trae otro técnico. Quién hizo el cambio lo guarda la bitácora (``fn_auditar``).
        """
        await self.obtener(actor, tram_id)
        cambios = s.model_dump(exclude_unset=True)
        if "atra_id" in cambios and cambios["atra_id"] is None:
            raise ReglaNegocio("El atrayente es obligatorio.", codigo="ATRAYENTE_INVALIDO")
        if "esta_id" in cambios:
            if cambios["esta_id"] is None:
                raise ReglaNegocio("El estado es obligatorio.", codigo="ESTADO_INVALIDO")
            if cambios.get("tecn_id_estado") is None:
                raise ReglaNegocio(
                    "Indique el técnico que cambia el estado de la trampa (tecn_id_estado).",
                    codigo="TECNICO_ESTADO_REQUERIDO",
                )
        if cambios:
            await self._repo.actualizar_trampa(tram_id, cambios)
        return await self.obtener(actor, tram_id)

    async def ubicaciones(self, actor: Actor, tram_id: int) -> list[e.UbicacionTrampa]:
        """Historial de ubicaciones, de la más reciente a la más antigua (la vigente no tiene
        ``ubic_fecha_fin``). Nunca se borra: reubicar cierra la anterior y agrega otra.

        Solo se devuelven las ubicaciones en lotes visibles para el actor; si no queda ninguna -> 404
        (no 403). No se usa ``trampa_por_id`` porque una trampa retirada (sin ubicación vigente)
        también tiene historial.
        """
        filas = await self._repo.ubicaciones(tram_id, actor.usua_id)
        if not filas:
            raise NoEncontrado(
                "La trampa no existe o no es visible para usted.", codigo="TRAMPA_NO_ENCONTRADA"
            )
        return [e.UbicacionTrampa(**fila) for fila in filas]

    async def reubicar(self, actor: Actor, tram_id: int, s: e.NuevaUbicacion) -> e.UbicacionTrampa:
        """Mueve la trampa a otro punto o lote: cierra la ubicación vigente con ``ubic_fecha_fin``
        y registra la nueva. La BD hace cumplir vigencia y solape (EXCLUDE).

        Invalida el paquete de campo (pendiente: no se llama a ``invalidar_paquetes``).
        """
        trampa = await self._repo.trampa_por_id(actor.usua_id, tram_id)
        if trampa is None:
            raise NoEncontrado(
                "La trampa no existe o no es visible para usted.", codigo="TRAMPA_NO_ENCONTRADA"
            )
        pred_id = await self._predio_destino(actor, s)
        await self._exigir_distancia_minima(pred_id, s, excluir_tram_id=tram_id)
        ubic_id = await self._repo.reubicar(tram_id, {**s.model_dump(), "pred_id": pred_id})
        filas = await self._repo.ubicaciones(tram_id, actor.usua_id)
        for fila in filas:
            if fila["ubic_id"] == ubic_id:
                return e.UbicacionTrampa(**fila)
        raise NoEncontrado("La trampa no existe o no es visible para usted.", codigo="TRAMPA_NO_ENCONTRADA")


class ServicioPrograma:
    def __init__(self, repo: RepositorioConfiguracion, cola: Cola) -> None:
        self._repo = repo
        self._cola = cola

    async def vigente(self, actor: Actor, pred_id: int, fecha: date | None) -> e.ProgramaInspeccion:
        """Sin fecha = hoy en la zona horaria del predio. Con fecha = el que regía ese día (RF-024)."""
        visible = await self._repo.predio_visible(actor.usua_id, pred_id)
        if not visible:
            raise NoEncontrado("El predio no existe o no tiene acceso a él.", codigo="PREDIO_NO_ENCONTRADO")
        f = fecha or datetime.now(UTC).date()
        fila = await self._repo.programa_vigente(pred_id, f)
        if not fila:
            raise NoEncontrado(
                "El predio no tiene programa de inspección vigente.", codigo="SIN_PROGRAMA_VIGENTE"
            )
        return e.ProgramaInspeccion(**fila)

    async def de_lote(self, actor: Actor, lote_id: int) -> e.ProgramaInspeccion:
        """Móvil ``getInspectionConfig``."""
        raise NoImplementado("Programa del lote")

    async def versiones(self, actor: Actor, pred_id: int) -> list[e.ProgramaInspeccion]:
        raise NoImplementado("Versiones del programa")

    async def nueva_version(
        self, actor: Actor, pred_id: int, s: e.NuevaVersionPrograma
    ) -> e.ProgramaInspeccion:
        """Web ``saveNewVersion``. Reglas (fn_programa_valida + §5.3): vigente_desde no anterior a la
        versión vigente; RN-007 y RN-004 en rango; escenarios sin solape. NO cambia el pasado:
        los periodos ya calculados conservan su umbral (ADR-05). Invalida el paquete de campo."""
        raise NoImplementado("Nueva versión del programa")


class ServicioReticula:
    def __init__(self, repo: RepositorioConfiguracion, cola: Cola) -> None:
        self._repo = repo
        self._cola = cola

    async def obtener(self, actor: Actor, pred_id: int) -> e.Reticula:
        raise NoImplementado("Retícula")

    async def crear(self, actor: Actor, pred_id: int, s: e.NuevaReticula) -> e.Reticula:
        """422 'La retícula es menor que la distancia mínima entre trampas' si no cumple §5.3."""
        raise NoImplementado("Alta de retícula")


class ServicioPaqueteCampo:
    def __init__(self, repo: RepositorioConfiguracion) -> None:
        self._repo = repo

    async def info(self, actor: Actor, pred_id: int) -> e.PaqueteCampoInfo:
        visible = await self._repo.predio_visible(actor.usua_id, pred_id)
        if not visible:
            raise NoEncontrado("El predio no existe o no tiene acceso a él.", codigo="PREDIO_NO_ENCONTRADO")
        paquete = await self._repo.paquete_vigente(pred_id)
        if paquete is None:
            return await self.generar(pred_id, "Generación inicial bajo demanda")
        return e.PaqueteCampoInfo(**paquete)

    async def contenido(
        self, actor: Actor, pred_id: int, si_no_coincide: str | None
    ) -> e.PaqueteCampoContenido | None:
        """Devuelve None si ``If-None-Match`` coincide con el hash vigente (la ruta responde 304).
        RNF-020: tamaño acotado; el mapa base NO va aquí: el móvil lo descarga del proveedor
        según ``mapa_base.permite_descarga`` (licencia, UQQ-007)."""
        visible = await self._repo.predio_visible(actor.usua_id, pred_id)
        if not visible:
            raise NoEncontrado("El predio no existe o no tiene acceso a él.", codigo="PREDIO_NO_ENCONTRADO")
        paquete = await self._repo.paquete_vigente(pred_id)
        if paquete is None:
            info = await self.generar(pred_id, "Generación inicial bajo demanda")
            paquete = info.model_dump()

        paqu_hash = paquete["paqu_hash"].strip()
        if si_no_coincide is not None:
            etag = si_no_coincide.strip().strip('"')
            if etag == paqu_hash:
                return None

        predio_dict = await self._repo.predio(pred_id)
        if not predio_dict:
            raise NoEncontrado("El predio no existe.", codigo="PREDIO_NO_ENCONTRADO")
        predio = e.Predio(**predio_dict)

        hoy = datetime.now(UTC).date()
        ahora = datetime.now(UTC)
        prog_dict = await self._repo.programa_vigente(pred_id, hoy)
        if not prog_dict:
            raise ReglaNegocio(
                "El predio no tiene un programa de inspección vigente.", codigo="SIN_PROGRAMA_VIGENTE"
            )
        programa = e.ProgramaInspeccion(**prog_dict)

        mapa_dict = await self._repo.mapa_predio(pred_id, ahora)
        mapa = e.MapaPredio(**mapa_dict)

        catalogos = await self._repo.catalogos_paquete()
        limites = await self._repo.limites_predio(pred_id)
        mapa_base = e.FuenteMapaBase(
            estilo_url=None,
            plantillas_teselas=[],
            atribucion="© SIGAT / Instituto Colombiano Agropecuario",
            permite_descarga=False,
            zoom_min=12,
            zoom_max=18,
            limites=limites,
        )

        return e.PaqueteCampoContenido(
            paqu_version=paquete["paqu_version"],
            paqu_hash=paqu_hash,
            generado_en=paquete["generado_en"],
            predio=predio,
            programa=programa,
            mapa=mapa,
            catalogos=catalogos,
            mapa_base=mapa_base,
        )

    async def generar(self, pred_id: int, motivo: str) -> e.PaqueteCampoInfo:
        """Tarea de la cola ``generar_paquete_campo``: invalida el anterior y registra uno nuevo."""
        predio_dict = await self._repo.predio(pred_id)
        if not predio_dict:
            raise NoEncontrado("El predio no existe.", codigo="PREDIO_NO_ENCONTRADO")
        predio = e.Predio(**predio_dict)

        hoy = datetime.now(UTC).date()
        ahora = datetime.now(UTC)
        prog_dict = await self._repo.programa_vigente(pred_id, hoy)
        if not prog_dict:
            raise ReglaNegocio(
                "El predio no tiene un programa de inspección vigente para generar el paquete.",
                codigo="SIN_PROGRAMA_VIGENTE",
            )
        programa = e.ProgramaInspeccion(**prog_dict)

        mapa_dict = await self._repo.mapa_predio(pred_id, ahora)
        mapa = e.MapaPredio(**mapa_dict)

        catalogos = await self._repo.catalogos_paquete()
        limites = await self._repo.limites_predio(pred_id)
        mapa_base = e.FuenteMapaBase(
            estilo_url=None,
            plantillas_teselas=[],
            atribucion="© SIGAT / Instituto Colombiano Agropecuario",
            permite_descarga=False,
            zoom_min=12,
            zoom_max=18,
            limites=limites,
        )

        await self._repo.invalidar_paquetes(pred_id, motivo)

        payload = {
            "predio": predio.model_dump(mode="json"),
            "programa": programa.model_dump(mode="json"),
            "mapa": mapa.model_dump(mode="json"),
            "catalogos": catalogos,
            "mapa_base": mapa_base.model_dump(mode="json"),
        }
        hash_hex = hashlib.sha256(rfc8785.dumps(payload)).hexdigest()

        paquete_info = await self._repo.registrar_paquete(
            pred_id=pred_id,
            prog_id=programa.prog_id,
            hash_hex=hash_hex,
            generado_en=ahora,
        )
        return e.PaqueteCampoInfo(**paquete_info)
