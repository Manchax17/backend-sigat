"""Reglas del módulo notificaciones (RNF-017).

El módulo SÍ está implementado (``listar``, ``conteo``, ``marcar_leida``, ``emitir``):

* el servicio se prueba con un doble en memoria que filtra por ``usua_id`` (como el
  ``WHERE n.usua_id = :u`` del SQL real), y
* una conexión falsa que captura el SQL crudo comprueba que el repositorio real mantiene
  el filtro por el dueño y la idempotencia por evento (``ON CONFLICT ... DO NOTHING``).

La deduplicación efectiva la impone PostgreSQL; aquí se verifica que el SQL que el
backend emitiría la incluye.
"""

from datetime import UTC, datetime
from typing import Any

import pytest

from sigat_api.compartido.errores import NoEncontrado
from sigat_api.compartido.tipos import Actor, Cliente, RolCodigo
from sigat_api.modules.notificaciones.repositorio import RepositorioNotificaciones
from sigat_api.modules.notificaciones.servicio import ServicioNotificaciones


def _actor(usua_id: int) -> Actor:
    return Actor(usua_id=usua_id, rol=RolCodigo.PRODUCTOR, disp_id=None, cliente=Cliente.WEB)


def _aviso(
    noti_id: int,
    usua_id: int,
    *,
    leida: bool = False,
    ruta: str | None = "/predios/9",
    cate_codigo: str = "avisos_predio",
) -> dict[str, Any]:
    return {
        "noti_id": noti_id,
        "cate_codigo": cate_codigo,
        "noti_titulo": f"Aviso {noti_id}",
        "noti_cuerpo": f"Cuerpo {noti_id}",
        "noti_creada_en": datetime(2026, 11, (noti_id % 28) + 1, tzinfo=UTC),
        "noti_leida_en": datetime(2026, 11, (noti_id % 28) + 1, 1, tzinfo=UTC) if leida else None,
        "noti_destino_ruta": ruta,
        "noti_destino_argumento": "9" if ruta else None,
        "usua_id": usua_id,
    }


def _plano(sql: str) -> str:
    return " ".join(sql.split())


class RepoAvisosFalso:
    """Doble en memoria de ``RepositorioNotificaciones``: filtra por el dueño como el SQL real."""

    def __init__(self) -> None:
        self.filas: list[dict[str, Any]] = []
        self.usuarios_consultados: list[int] = []
        self.claves_evento: set[tuple[int, str]] = set()
        self.creados: list[dict[str, Any]] = []

    async def listar(
        self, usua_id: int, solo_no_leidas: bool, antes_de: int | None, limite: int
    ) -> list[dict[str, Any]]:
        self.usuarios_consultados.append(usua_id)
        filas = [f for f in self.filas if f["usua_id"] == usua_id]
        if solo_no_leidas:
            filas = [f for f in filas if f["noti_leida_en"] is None]
        if antes_de is not None:
            filas = [f for f in filas if f["noti_id"] < antes_de]
        return sorted(filas, key=lambda f: int(str(f["noti_id"])), reverse=True)[:limite]

    async def marcar_leida(self, usua_id: int, noti_id: int, momento: datetime) -> bool:
        for f in self.filas:
            if f["usua_id"] == usua_id and f["noti_id"] == noti_id:
                f["noti_leida_en"] = momento
                return True
        return False

    async def crear(
        self,
        usua_id: int,
        categoria: str,
        titulo: str,
        cuerpo: str,
        evento_clave: str,
        ruta: str | None,
        argumento: str | None,
    ) -> bool:
        if (usua_id, evento_clave) in self.claves_evento:
            return False
        self.claves_evento.add((usua_id, evento_clave))
        self.creados.append({"usua_id": usua_id, "evento_clave": evento_clave, "titulo": titulo})
        return True


class ConexionFalsa:
    """``AsyncConnection`` mínima que graba el SQL emitido por el repositorio real."""

    def __init__(self, escalares: list[object] | None = None) -> None:
        self.consultas: list[tuple[str, dict[str, Any] | None]] = []
        self._escalares = list(escalares or [])

    async def execute(self, comando: Any, parametros: dict[str, Any] | None = None) -> Any:
        self.consultas.append((str(comando.text), parametros))
        return _ResultadoFalso()

    async def scalar(self, comando: Any, parametros: dict[str, Any] | None = None) -> Any:
        self.consultas.append((str(comando.text), parametros))
        return self._escalares.pop(0) if self._escalares else None


class _ResultadoFalso:
    def mappings(self) -> list[dict[str, Any]]:
        return []


@pytest.mark.regla
async def test_notificaciones_01() -> None:
    """solo se listan los avisos del actor."""
    repo = RepoAvisosFalso()
    repo.filas = [
        _aviso(1, 1),  # propio, sin leer
        _aviso(2, 1, leida=True),  # propio, leído
        _aviso(3, 2, cate_codigo="sincronizacion"),  # de OTRO usuario
    ]

    pagina = await ServicioNotificaciones(repo).listar(_actor(1), solo_no_leidas=False, cursor=None)

    assert repo.usuarios_consultados == [1]  # filtra POR el actor, no "todos"
    assert [n.noti_id for n in pagina.items] == [2, 1]  # más reciente primero
    assert 3 not in [n.noti_id for n in pagina.items]  # nunca el aviso ajeno
    assert [n.leida for n in pagina.items] == [True, False]
    assert pagina.items[1].destino is not None
    assert pagina.items[1].destino.ruta == "/predios/9"
    assert pagina.siguiente is None

    # El repositorio real filtra en SQL por el dueño del aviso.
    con = ConexionFalsa()
    await RepositorioNotificaciones(con).listar(1, False, None, 51)  # type: ignore[arg-type]
    sql = _plano(con.consultas[0][0])
    assert "WHERE n.usua_id = :u" in sql
    assert con.consultas[0][1] is not None
    assert con.consultas[0][1]["u"] == 1


@pytest.mark.regla
async def test_notificaciones_02() -> None:
    """marcar leído un aviso ajeno responde 404."""
    repo = RepoAvisosFalso()
    repo.filas = [_aviso(1, 1), _aviso(201, 2)]  # 201 es de otro usuario

    with pytest.raises(NoEncontrado) as error:
        await ServicioNotificaciones(repo).marcar_leida(_actor(1), 201)

    assert error.value.estado_http == 404  # 404, no 403: no revela que el aviso existe
    assert error.value.codigo == "AVISO_NO_ENCONTRADO"
    assert repo.filas[1]["noti_leida_en"] is None  # el aviso ajeno NO se toca

    # El propio sí se marca.
    await ServicioNotificaciones(repo).marcar_leida(_actor(1), 1)
    assert repo.filas[0]["noti_leida_en"] is not None

    # El servidor SELECTa y UPDATea siempre con usua_id = el del actor.
    con = ConexionFalsa(escalares=[None])  # existe = None: filtrado por :u no encuentra fila ajena
    assert (
        await RepositorioNotificaciones(con).marcar_leida(1, 201, datetime(2026, 11, 15, tzinfo=UTC)) is False
    )  # type: ignore[arg-type]
    assert len(con.consultas) == 1  # sin UPDATE
    sql, params = con.consultas[0]
    assert "usua_id = :u" in _plano(sql)
    assert params == {"n": 201, "u": 1}

    con2 = ConexionFalsa(escalares=[True])
    assert (
        await RepositorioNotificaciones(con2).marcar_leida(1, 1, datetime(2026, 11, 15, tzinfo=UTC)) is True
    )  # type: ignore[arg-type]
    assert any(
        "UPDATE sigat.notificacion" in _plano(s) and "usua_id = :u" in _plano(s) for s, _ in con2.consultas
    )


@pytest.mark.regla
async def test_notificaciones_03() -> None:
    """un mismo evento no crea dos avisos."""
    repo = RepoAvisosFalso()
    servicio = ServicioNotificaciones(repo)

    primera = await servicio.emitir(1, "avisos_predio", "Título", "Cuerpo", "evento-7", "/predios/9", "9")
    segunda = await servicio.emitir(1, "avisos_predio", "Título", "Cuerpo", "evento-7", "/predios/9", "9")
    otra_categoria = await servicio.emitir(1, "sincronizacion", "Otro", "Cuerpo", "evento-7", None, None)

    assert primera is True
    assert segunda is False  # mismo evento + mismo usua_id: no se crea de nuevo
    assert otra_categoria is False  # la clave de evento manda, aunque cambie el texto/categoría
    assert len(repo.creados) == 1

    # otro usuario SÍ puede recibir el mismo evento (la clave es por persona)
    assert await servicio.emitir(2, "avisos_predio", "Título", "Cuerpo", "evento-7") is True

    # y la garantía real la impone el SQL del repositorio (ON CONFLICT ... DO NOTHING).
    con = ConexionFalsa(escalares=[1])
    assert (
        await RepositorioNotificaciones(con).crear(
            1, "avisos_predio", "Título", "Cuerpo", "evento-7", "/predios/9", "9"
        )
        is True
    )  # type: ignore[arg-type]
    sql = _plano(con.consultas[0][0])
    assert "ON CONFLICT (usua_id, noti_evento_clave) DO NOTHING" in sql
    assert con.consultas[0][1] is not None
    assert con.consultas[0][1]["k"] == "evento-7"
    assert con.consultas[0][1]["u"] == 1
