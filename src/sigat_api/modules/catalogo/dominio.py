"""Registro de catálogos expuestos: código público -> tabla, edición y columnas."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DefinicionCatalogo:
    codigo: str  # código en la URL /catalogos/{codigo}
    tabla: str  # tabla sigat
    prefijo: str  # prefijo de columnas SIGAT
    titulo: str
    editable: bool  # editable desde la web (admin/agrónomo)
    columna_activo: str | None  # None = no admite retiro lógico (solo lectura)


CATALOGOS: tuple[DefinicionCatalogo, ...] = (
    DefinicionCatalogo(
        "condicion_trampa", "condicion_trampa", "cond", "Condición de trampa", True, "cond_activa"
    ),
    DefinicionCatalogo("atrayente", "atrayente", "atra", "Atrayentes", True, None),
    DefinicionCatalogo("tipo_trampa", "tipo_trampa", "tipt", "Tipos de trampa", False, None),
    DefinicionCatalogo("especie_mosca", "especie_mosca", "espm", "Especies de mosca", False, None),
    DefinicionCatalogo(
        "producto_fitosanitario",
        "producto_fitosanitario",
        "prof",
        "Productos fitosanitarios",
        True,
        "prof_activo",
    ),
    DefinicionCatalogo("unidad_medida", "unidad_medida", "unid", "Unidades de medida", False, None),
    DefinicionCatalogo("plaga_enfermedad", "plaga_enfermedad", "plag", "Plagas y enfermedades", True, None),
    DefinicionCatalogo("severidad", "severidad", "seve", "Severidad", True, None),
    DefinicionCatalogo("criterio_bpa", "criterio_bpa", "crit", "Criterios BPA", True, "crit_activo"),
    DefinicionCatalogo("tipo_manejo", "tipo_manejo", "tipm", "Tipos de manejo agronómico", True, None),
    DefinicionCatalogo("fenologia", "fenologia", "feno", "Etapas fenológicas", True, None),
    DefinicionCatalogo("especie_cultivo", "especie_cultivo", "espc", "Especies de cultivo", False, None),
    DefinicionCatalogo("variedad_cultivo", "variedad_cultivo", "vari", "Variedades", True, None),
    DefinicionCatalogo(
        "escenario_trampeo", "escenario_trampeo", "esce", "Escenarios de trampeo", False, None
    ),
    DefinicionCatalogo("vigilancia", "vigilancia", "vigi", "Tipos de vigilancia", False, None),
    DefinicionCatalogo("localizacion", "localizacion", "loca", "Localización de trampa", True, None),
    DefinicionCatalogo("arbol", "arbol", "arbo", "Hospedero (árbol)", True, None),
    DefinicionCatalogo("norma", "norma", "norm", "Normas", False, None),
    DefinicionCatalogo("centro_acopio", "centro_acopio", "cent", "Centros de acopio", False, None),
    DefinicionCatalogo("estado_registro", "estado_registro", "esta", "Estados de registro", False, None),
)

POR_CODIGO = {c.codigo: c for c in CATALOGOS}
