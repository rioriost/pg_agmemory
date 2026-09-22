
-- Distribution identity only; meaningful only with a trusted pinned image.
CREATE FUNCTION ag_catalog.pgag_age_build()
RETURNS pg_catalog.jsonb
LANGUAGE sql
IMMUTABLE
SECURITY INVOKER
RETURN '{"commit":"72707aab7ce982bf13cad3d102bd869dab07d64b","upstream_base_commit":"fa109ef1ddb1c7a945a1c340195d650000e49713","patch_sha256":"89b711f33234a304476d3164b578f8da700a527a6668782823c694695efc1653"}'::pg_catalog.jsonb;
