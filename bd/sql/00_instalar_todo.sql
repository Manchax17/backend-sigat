-- =====================================================================
-- SIGAT v9.1 - INSTALACIÓN COMPLETA EN UNA SOLA ORDEN (base nueva o de pruebas)
--   psql -X -v ON_ERROR_STOP=1 -f 00_instalar_todo.sql                 # esquema + catálogos + vistas + funciones + validación
-- Ejecutar DESDE la carpeta sql/ (usa \ir). Copia del paquete de BD SIGAT v9 SIN datos de demostración.
-- =====================================================================
\set ON_ERROR_STOP on
\ir 01_esquema_completo.sql
\ir 02_catalogos_basicos.sql
\ir 03_vistas.sql
\ir 04_funciones.sql
\ir 08_ajustes_v8.sql
\ir 09_validacion_v8.sql
\ir 10_ajustes_v9.sql
\ir 11_validacion_v9.sql
\ir 12_ajustes_v9_1.sql
\ir 13_vistas_productor.sql
\ir 14_semilla_escenarios_provisionales.sql
\ir 15_semilla_permisos.sql
\ir 16_regla_cosecha_abierta.sql
\ir 06_validacion.sql
\ir 17_vistas_informe_local.sql
\echo 'SIGAT v9 instalado (sin cuentas de usuario: cree el primer administrador con el backend).'
