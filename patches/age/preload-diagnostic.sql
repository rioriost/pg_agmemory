
-- Narrow diagnostic definer, not a data-access or RLS-authorization bypass.
CREATE FUNCTION ag_catalog.pgag_age_preloaded()
RETURNS pg_catalog.bool
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $pgag_preloaded$
    SELECT EXISTS (
        SELECT FROM unnest(string_to_array(current_setting('shared_preload_libraries'), ','))
            AS x(name)
        WHERE btrim(name) = 'age'
    )
$pgag_preloaded$;
REVOKE ALL ON FUNCTION ag_catalog.pgag_age_preloaded() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ag_catalog.pgag_age_preloaded() TO PUBLIC;
COMMENT ON FUNCTION ag_catalog.pgag_age_preloaded() IS
    'Fixed-setting, no-argument boolean preload diagnostic only; not a data or RLS authorization bypass.';
