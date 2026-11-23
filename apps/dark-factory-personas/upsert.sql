SET search_path TO dark_factory;
BEGIN;

-- Fails (no rows for \gset) when the provider does not exist.
SELECT id AS provider_id FROM model_providers WHERE name = :'provider' \gset

-- The model must be a model of the provider; register it if it is missing. Limits are set when
-- given (context and max_output together) and left alone otherwise.
INSERT INTO model_provider_models (provider_id, model_id, wire_api, context_window_tokens, max_output_tokens)
VALUES (:'provider_id', :'model', :'wire_api',
        NULLIF(:'context', '')::bigint, NULLIF(:'max_output', '')::bigint)
ON CONFLICT (provider_id, model_id) DO UPDATE SET
    context_window_tokens = EXCLUDED.context_window_tokens,
    max_output_tokens = EXCLUDED.max_output_tokens
WHERE EXCLUDED.context_window_tokens IS NOT NULL
  AND (model_provider_models.context_window_tokens, model_provider_models.max_output_tokens)
      IS DISTINCT FROM (EXCLUDED.context_window_tokens, EXCLUDED.max_output_tokens);

-- Dark Factory uses UUIDv7 ids; PostgreSQL 17 has no uuidv7(), so build one.
INSERT INTO personas (id, name, description, system_prompt, harness, model, model_provider_id)
VALUES (
    encode(
        set_bit(set_bit(overlay(uuid_send(gen_random_uuid())
            placing substring(int8send(floor(extract(epoch FROM clock_timestamp()) * 1000)::bigint) FROM 3)
            FROM 1 FOR 6), 52, 1), 53, 1),
        'hex')::uuid,
    :'name', :'description', :'prompt', :'harness', :'model', :'provider_id')
ON CONFLICT (name) DO UPDATE SET
    description = EXCLUDED.description,
    system_prompt = EXCLUDED.system_prompt,
    harness = EXCLUDED.harness,
    model = EXCLUDED.model,
    model_provider_id = EXCLUDED.model_provider_id,
    updated_at = now()
WHERE (personas.description, personas.system_prompt, personas.harness, personas.model,
       personas.model_provider_id)
      IS DISTINCT FROM
      (EXCLUDED.description, EXCLUDED.system_prompt, EXCLUDED.harness, EXCLUDED.model,
       EXCLUDED.model_provider_id);

COMMIT;
