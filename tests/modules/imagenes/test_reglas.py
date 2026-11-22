"""Reglas del módulo imagenes (RNF-017).

Todo el servicio de archivos es esqueleto: ``declarar``, ``subir_contenido``,
``confirmar_subida``, ``obtener``, ``exigir_almacenado``... lanzan ``NoImplementado``,
igual que ``AlmacenObjetos`` y la tarea de cola ``verificar_archivo``. Las reglas 01-03
viven en ese código que aún no existe (la validación por firma de bytes tendría que
añadirse en ``subir_contenido``), así que se dejan con skip y motivo; NO se inventa.

Lo único existente en Python es el contrato de entrada (``NuevoArchivo``): ``arch_mime``
es un ``Literal`` (el cliente no puede declarar un MIME arbitrario) y ``arch_hash_sha256``
exige 64 hex. Eso NO cubre las tres reglas (ninguna comprueba el CONTENIDO ni la
propiedad del archivo), así que no se usan como sustituto.
"""

import pytest


@pytest.mark.regla
@pytest.mark.skip(
    reason="ServicioArchivos.declarar/subir_contenido lanzan NoImplementado y la tarea de cola "
    "``verificar_archivo`` (recalcula SHA-256 y marca almacenado/rechazado) tampoco está programada: "
    "no existe código Python que compare el hash declarado con el del contenido recibido."
)
def test_imagenes_01() -> None:
    """archivo con hash distinto al declarado queda rechazado."""


@pytest.mark.regla
@pytest.mark.skip(
    reason="No hay sniffing de firma de bytes (magic numbers) en el backend: el MIME se valida hoy solo "
    "como Literal declarado por el cliente en NuevoArchivo; la verificación «por contenido y no por "
    "extensión» tendría que vivir en subir_contenido(), hoy NoImplementado."
)
def test_imagenes_02() -> None:
    """MIME se valida por contenido y no por extensión."""


@pytest.mark.regla
@pytest.mark.skip(
    reason="La regla la aplica ServicioPerfil.poner_foto (identidad) apoyándose en "
    "ServicioArchivos.exigir_almacenado: ambos lanzan NoImplementado. No hay camino Python que rechace "
    "un archivo de otro usuario como foto de perfil."
)
def test_imagenes_03() -> None:
    """foto de otro usuario no puede usarse como foto de perfil."""
