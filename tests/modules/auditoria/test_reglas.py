"""Reglas del módulo auditoría (RNF-017).

El módulo está a medio implementar: ``ServicioAuditoria.listar`` lanza
``NoImplementado`` en este backend (el conteo y los filtros SQL aún no existen).
Se prueba lo que SÍ existe en Python:

* la puerta de autorización que usa la ruta ``GET /auditoria``
  (``requiere("auditoria.consultar")`` sobre la matriz RNF-015): solo el administrador;
* la proyección pública de la bitácora (``EntradaAuditoria``): campos serializados
  sin contraseñas ni hashes (la base tiene ``extra="forbid"``: rechaza, no silencia).
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from sigat_api.api.dependencias import requiere
from sigat_api.compartido.errores import Prohibido
from sigat_api.compartido.tipos import Actor, Cliente, RolCodigo
from sigat_api.modules.auditoria.esquemas import EntradaAuditoria
from sigat_api.modules.identidad.permisos import permitido


def _actor(rol: RolCodigo) -> Actor:
    return Actor(usua_id=7, rol=rol, disp_id=None, cliente=Cliente.WEB)


def _fila_bita() -> dict[str, object]:
    return {
        "bita_id": 1,
        "bita_t_registro": datetime(2026, 11, 14, 9, 30, 0, tzinfo=UTC),
        "usua_id": 7,
        "usuario": "admin.uno",
        "bita_tabla": "usuario",
        "bita_registro": "7",
        "bita_accion": "INSERT",
        "categoria": "Usuarios",
        "resumen": "Usuario creado · admin.uno",
    }


@pytest.mark.regla
async def test_auditoria_01() -> None:
    """solo el administrador consulta la bitácora."""
    dependencia = requiere("auditoria.consultar")

    for rol in (RolCodigo.AGRONOMO_SUPERVISOR, RolCodigo.TECNICO, RolCodigo.PRODUCTOR):
        with pytest.raises(Prohibido) as error:
            await dependencia(_actor(rol))
        assert error.value.estado_http == 403
        assert error.value.codigo == "ROL_SIN_PERMISO"

    actor = await dependencia(_actor(RolCodigo.ADMINISTRADOR))
    assert actor.rol is RolCodigo.ADMINISTRADOR
    assert permitido("auditoria.consultar", RolCodigo.ADMINISTRADOR)
    assert not any(
        permitido("auditoria.consultar", rol)
        for rol in (RolCodigo.AGRONOMO_SUPERVISOR, RolCodigo.TECNICO, RolCodigo.PRODUCTOR)
    )


@pytest.mark.regla
async def test_auditoria_02() -> None:
    """la bitácora nunca expone contraseñas ni hashes."""
    sensibles = ("contrasena", "password", "clave", "hash", "salt", "token", "secreto", "pin")

    # 1. La proyección pública no declara NINGÚN campo sensible (ni siquiera hasheado).
    campos = {nombre.lower() for nombre in EntradaAuditoria.model_fields}
    assert not any(sensible in campo for campo in campos for sensible in sensibles)

    # 2. Lo que viaja al front (model_dump / JSON) tampoco los contiene.
    entrada = EntradaAuditoria.model_validate(_fila_bita())
    en_memoria = {k.lower() for k in entrada.model_dump()}
    en_json = entrada.model_dump_json().lower()
    assert not any(sensible in k for k in en_memoria for sensible in sensibles)
    assert not any(sensible in en_json for sensible in sensibles)

    # 3. Fail-closed: una fila SQL con columnas sensibles extra se rechaza (extra="forbid"),
    #    no se serializa en silencio.
    with pytest.raises(ValidationError):
        EntradaAuditoria.model_validate({**_fila_bita(), "usua_contrasena": "$argon2id$v=19$..."})
    with pytest.raises(ValidationError):
        EntradaAuditoria.model_validate(
            {**_fila_bita(), "contrasena_hash": "9f2b..."}  # pragma: allowlist secret
        )
