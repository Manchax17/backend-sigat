"""Instalación desde cero, primer administrador, inicio de sesión y permisos contra PostgreSQL REAL.

Se ejecuta solo si existe ``SIGAT_DB_URL_PRUEBAS`` (servidor PostgreSQL/PostGIS; ver ``conftest.py``)::

    SIGAT_DB_URL_PRUEBAS="postgresql+asyncpg://postgres@localhost:5432/postgres" pytest -m integracion

Las pruebas de este módulo comparten UNA base recién creada y van en orden: primero se verifica que
la instalación no trae cuentas, luego se crea el administrador con el comando real y con él el resto.
Ninguna contraseña está escrita en el repositorio: se generan al azar en cada ejecución.
"""

import secrets
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integracion
P = "/api/v1"
CLAVE_ADMIN = secrets.token_urlsafe(16)
CLAVES: dict[str, str] = {}


@pytest.fixture(scope="module")
def api(bd_limpia: str, h: SimpleNamespace) -> Iterator[TestClient]:
    from sigat_api.config import obtener_configuracion
    from sigat_api.infra.db import motor

    mp = pytest.MonkeyPatch()
    mp.setenv("SIGAT_DB_URL", h.url(bd_limpia))
    mp.setenv("SIGAT_COOKIE_SEGURA", "false")
    obtener_configuracion.cache_clear()
    motor._motor = None
    from sigat_api.main import crear_app

    with TestClient(crear_app()) as c:
        yield c
    mp.undo()
    obtener_configuracion.cache_clear()
    motor._motor = None


def _login(api: TestClient, usuario: str, clave: str, cliente: str = "web") -> dict[str, Any]:
    r = api.post(f"{P}/auth/login", json={"usuario": usuario, "contrasena": clave, "cliente": cliente})
    return {"estado": r.status_code, **r.json()}


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _admin(api: TestClient) -> dict[str, str]:
    r = _login(api, "admin.local", CLAVE_ADMIN)
    assert r["estado"] == 200, r
    return _auth(r["token"])


ARGS_ADMIN = (
    "inicializar-admin", "--cedula", "1000000001", "--nombre", "Admin", "--apellido", "Local",
    "--usuario", "admin.local", "--correo", "admin.local@ejemplo.test",
    "--centro", "Centro de pruebas", "--departamento-codigo", "99", "--departamento", "Pruebas",
    "--contrasena-stdin",
)  # fmt: skip


# ---------------------------------------------------------------- instalación limpia
def test_instalacion_limpia_no_trae_cuentas(bd_limpia: str, h: SimpleNamespace) -> None:
    assert h.consulta(bd_limpia, "SELECT count(*) FROM sigat.usuario") == [(0,)]
    assert h.consulta(bd_limpia, "SELECT count(*) FROM sigat.persona") == [(0,)]
    assert h.consulta(bd_limpia, "SELECT count(*) FROM sigat.restablecimiento_clave") == [(0,)]
    # Los catálogos legítimos sí vienen (roles, estados, categorías de aviso).
    roles = {r[0] for r in h.consulta(bd_limpia, "SELECT rol_codigo FROM sigat.rol")}
    assert roles == {"ADMINISTRADOR", "AGRONOMO_SUPERVISOR", "TECNICO", "PRODUCTOR"}
    assert h.consulta(
        bd_limpia, "SELECT 1 FROM sigat.categoria_notificacion WHERE cate_codigo = 'solicitudes_cuenta'"
    ) == [(1,)]
    assert h.consulta(bd_limpia, "SELECT version_num FROM api.alembic_version") == [("0008",)]


def test_sin_cuentas_nadie_entra(api: TestClient) -> None:
    for usuario in ("admin", "administrador", "agronomo", "tecnico", "productor", "admin.local"):
        assert _login(api, usuario, "Sigat2026*")["codigo"] == "CREDENCIALES_INVALIDAS"


# ---------------------------------------------------------------- primer administrador
def test_primer_admin_rechaza_produccion_sin_confirmar(bd_limpia: str, h: SimpleNamespace) -> None:
    r = h.cli(
        bd_limpia,
        *ARGS_ADMIN,
        entrada=CLAVE_ADMIN + "\n",
        SIGAT_ENTORNO="produccion",
        SIGAT_COOKIE_SEGURA="true",
    )
    assert r.returncode == 2, r.stderr
    assert "--entorno-produccion" in r.stderr
    assert h.consulta(bd_limpia, "SELECT count(*) FROM sigat.usuario") == [(0,)]


def test_primer_admin_rechaza_clave_corta(bd_limpia: str, h: SimpleNamespace) -> None:
    r = h.cli(bd_limpia, *ARGS_ADMIN, entrada="corta\n")
    assert r.returncode == 2
    assert h.consulta(bd_limpia, "SELECT count(*) FROM sigat.usuario") == [(0,)]


def test_primer_admin_se_crea_con_hash_argon2id(bd_limpia: str, h: SimpleNamespace) -> None:
    r = h.cli(bd_limpia, *ARGS_ADMIN, entrada=CLAVE_ADMIN + "\n")
    assert r.returncode == 0, r.stderr
    assert CLAVE_ADMIN not in r.stdout + r.stderr
    [(hash_, rol)] = h.consulta(
        bd_limpia,
        "SELECT u.usua_contrasena, r.rol_codigo FROM sigat.usuario u JOIN sigat.rol r USING (rol_id)",
    )
    assert str(hash_).startswith("$argon2id$")
    assert CLAVE_ADMIN not in str(hash_)
    assert rol == "ADMINISTRADOR"


def test_primer_admin_no_se_puede_repetir(bd_limpia: str, h: SimpleNamespace) -> None:
    r = h.cli(
        bd_limpia,
        *[a if a != "admin.local" else "intruso" for a in ARGS_ADMIN],
        entrada=secrets.token_urlsafe(16) + "\n",
    )
    assert r.returncode == 2
    assert "ya tiene un administrador" in r.stderr
    assert h.consulta(bd_limpia, "SELECT count(*) FROM sigat.usuario") == [(1,)]


def test_no_existe_ruta_publica_para_crear_cuentas(api: TestClient) -> None:
    cuerpo = {"pers_cc": 1, "nombre": "X", "apellido": "Y", "usuario": "nuevo", "rol": "ADMINISTRADOR"}
    assert api.post(f"{P}/usuarios", json=cuerpo).status_code == 401
    rutas = set(api.get(f"{P}/openapi.json").json()["paths"])
    assert f"{P}/usuarios" in rutas
    # Las únicas rutas públicas de /auth son las de sesión y recuperación: ninguna crea cuentas.
    publicas = {r.removeprefix(f"{P}/auth/") for r in rutas if r.startswith(f"{P}/auth/")}
    assert publicas <= {
        "login",
        "refresco",
        "logout",
        "sesion",
        "recuperacion",
        "recuperacion/confirmar",
        "contrasena",
    }
    assert not any("signup" in r or "inicializar" in r or "bootstrap" in r for r in rutas)


# ---------------------------------------------------------------- login y permisos
def test_admin_crea_cuentas_y_cada_rol_entra_por_su_cliente(api: TestClient) -> None:
    ha = _admin(api)
    cent = api.get(f"{P}/catalogos/centro_acopio", headers=ha).json()[0]["id"]
    nuevos = [
        ("agro.prueba", "AGRONOMO_SUPERVISOR", 1000000002, {}),
        ("tec.prueba", "TECNICO", 1000000003, {"cent_id": cent}),
    ]
    for usuario, rol, cc, datos_rol in nuevos:
        CLAVES[usuario] = secrets.token_urlsafe(14)
        r = api.post(
            f"{P}/usuarios",
            headers=ha,
            json={
                "pers_cc": cc, "nombre": "Prueba", "apellido": rol.title(), "usuario": usuario,
                "rol": rol, "datos_rol": datos_rol, "contrasena_temporal": CLAVES[usuario],
            },
        )  # fmt: skip
        assert r.status_code == 201, r.json()
        assert r.json()["requiere_cambio_contrasena"] is True

    agro = _login(api, "agro.prueba", CLAVES["agro.prueba"])
    assert agro["estado"] == 200
    assert agro["user"]["rol"] == "AGRONOMO_SUPERVISOR"
    assert _login(api, "tec.prueba", CLAVES["tec.prueba"], "movil")["user"]["rol"] == "TECNICO"
    assert _login(api, "tec.prueba", CLAVES["tec.prueba"], "web")["codigo"] == "ROL_SOLO_MOVIL"
    assert _login(api, "agro.prueba", "incorrecta-123")["codigo"] == "CREDENCIALES_INVALIDAS"

    # Un rol que no es administrador no gestiona usuarios.
    r = api.get(f"{P}/usuarios", headers=_auth(agro["token"]))
    assert r.status_code == 403
    assert r.json()["codigo"] == "ROL_SIN_PERMISO"
    assert api.get(f"{P}/usuarios", headers=ha).status_code == 200


def test_duplicados_dan_409_con_codigo_estable(api: TestClient) -> None:
    ha = _admin(api)
    base = {"nombre": "X", "apellido": "Y", "rol": "AGRONOMO_SUPERVISOR"}
    r = api.post(f"{P}/usuarios", headers=ha, json={**base, "pers_cc": 1000000009, "usuario": "AGRO.PRUEBA"})
    assert (r.status_code, r.json()["codigo"]) == (409, "USUARIO_DUPLICADO")
    r = api.post(f"{P}/usuarios", headers=ha, json={**base, "pers_cc": 1000000002, "usuario": "otro.x"})
    assert (r.status_code, r.json()["codigo"]) == (409, "CEDULA_DUPLICADA")


def test_sesion_refresco_por_cookie_y_cierre(api: TestClient) -> None:
    r = _login(api, "agro.prueba", CLAVES["agro.prueba"])
    yo = api.get(f"{P}/auth/sesion", headers=_auth(r["token"]))
    assert yo.status_code == 200
    assert yo.json()["usuario"] == "agro.prueba"
    nuevo = api.post(f"{P}/auth/refresco", json={})  # la cookie la guarda el TestClient
    assert nuevo.status_code == 200
    salida = api.post(f"{P}/auth/logout", headers=_auth(nuevo.json()["token"]))
    assert salida.status_code == 204
    assert api.post(f"{P}/auth/refresco", json={}).status_code == 401


# ---------------------------------------------------------------- olvidé mi contraseña
def test_solicitud_avisa_al_admin_y_el_admin_asigna_la_clave(api: TestClient) -> None:
    import time

    viejo = _login(api, "agro.prueba", CLAVES["agro.prueba"])["token"]
    for identificador in ("agro.prueba", "agro.prueba", "no.existe"):
        r = api.post(f"{P}/auth/recuperacion", json={"identificador": identificador})
        assert r.status_code == 202  # misma respuesta exista o no la cuenta
    ha = _admin(api)
    avisos = api.get(f"{P}/yo/notificaciones", headers=ha).json()["items"]
    solicitudes = [a for a in avisos if a["categoria"] == "solicitudes_cuenta"]
    assert len(solicitudes) == 1  # la segunda solicitud no duplica el aviso
    usua_id = int(solicitudes[0]["destino"]["argumento"])
    assert solicitudes[0]["destino"]["ruta"] == "/admin/usuarios"
    assert "agro.prueba" in solicitudes[0]["cuerpo"]

    time.sleep(1.1)  # el token viejo debe ser anterior a la revocación (``iat`` va en segundos)
    nueva = secrets.token_urlsafe(14)
    r = api.post(
        f"{P}/usuarios/{usua_id}/restablecimiento-contrasena", headers=ha, json={"nueva_contrasena": nueva}
    )
    assert r.status_code == 204, r.json()
    assert api.get(f"{P}/auth/sesion", headers=_auth(viejo)).json()["codigo"] == "SESION_REVOCADA"
    assert _login(api, "agro.prueba", CLAVES["agro.prueba"])["codigo"] == "CREDENCIALES_INVALIDAS"
    entra = _login(api, "agro.prueba", nueva)
    assert entra["estado"] == 200
    assert entra["user"]["requiere_cambio_contrasena"] is True
    CLAVES["agro.prueba"] = nueva


def test_desactivar_corta_el_acceso_al_instante(api: TestClient) -> None:
    ha = _admin(api)
    tec = _login(api, "tec.prueba", CLAVES["tec.prueba"], "movil")
    usua_id = tec["user"]["id"]
    assert (
        api.post(f"{P}/usuarios/{usua_id}/activacion", headers=ha, json={"activo": False}).status_code == 200
    )
    assert api.get(f"{P}/auth/sesion", headers=_auth(tec["token"])).status_code == 401
    assert _login(api, "tec.prueba", CLAVES["tec.prueba"], "movil")["estado"] == 401
    assert (
        api.post(f"{P}/usuarios/{usua_id}/activacion", headers=ha, json={"activo": True}).status_code == 200
    )


def test_admin_no_puede_quitarse_el_rol_ni_desactivarse(api: TestClient) -> None:
    ha = _admin(api)
    yo = api.get(f"{P}/auth/sesion", headers=ha).json()["id"]
    r = api.post(f"{P}/usuarios/{yo}/activacion", headers=ha, json={"activo": False})
    assert (r.status_code, r.json()["codigo"]) == (409, "AUTO_DESACTIVACION")
    r = api.post(f"{P}/usuarios/{yo}/eliminacion", headers=ha, json={"motivo": "Prueba de autoeliminación"})
    assert r.status_code == 409
