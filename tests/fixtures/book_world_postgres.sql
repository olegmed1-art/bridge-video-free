-- Disposable CI database only. Minimal existing-schema surface; no production DDL.
CREATE TABLE IF NOT EXISTS public.school (
 school_id uuid PRIMARY KEY, stable_name text UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS public.source (
 source_id uuid PRIMARY KEY, school_id uuid NOT NULL REFERENCES public.school,
 canonical_locator text NOT NULL, title text, source_type text DEFAULT 'book', status text DEFAULT 'active');
CREATE TABLE IF NOT EXISTS public.knowledge_item (
 knowledge_item_id uuid PRIMARY KEY, school_id uuid NOT NULL REFERENCES public.school,
 stable_key text NOT NULL, knowledge_type text NOT NULL, title text NOT NULL,
 UNIQUE(school_id,stable_key));
CREATE TABLE IF NOT EXISTS public.knowledge_version (
 knowledge_version_id uuid PRIMARY KEY, knowledge_item_id uuid NOT NULL REFERENCES public.knowledge_item,
 version_no int NOT NULL CHECK(version_no>0), content jsonb NOT NULL, authority_class text NOT NULL,
 review_status text NOT NULL, bidding_system_key text, method_version text, provenance jsonb NOT NULL,
 status text NOT NULL, agreement_scope jsonb DEFAULT '{}', level_scope jsonb DEFAULT '{}',
 effective_from timestamptz, effective_to timestamptz, UNIQUE(knowledge_item_id,version_no),
 CHECK(authority_class IN ('external','school_canon','research_candidate','school_practice')));
CREATE TABLE IF NOT EXISTS public.knowledge_version_source (
 knowledge_version_id uuid NOT NULL REFERENCES public.knowledge_version,
 source_id uuid NOT NULL REFERENCES public.source, relation_type text NOT NULL, source_locator jsonb NOT NULL,
 PRIMARY KEY(knowledge_version_id,source_id,relation_type));
CREATE TABLE IF NOT EXISTS public.changeset (
 changeset_id uuid PRIMARY KEY, command_id uuid NOT NULL, school_id uuid NOT NULL REFERENCES public.school,
 status text NOT NULL CHECK(status IN ('started','committed','failed','cancelled')),
 correlation_id uuid NOT NULL, committed_at timestamptz, UNIQUE(school_id,command_id),
 CHECK(status <> 'committed' OR committed_at IS NOT NULL));
CREATE TABLE IF NOT EXISTS public.domain_event (
 event_id uuid PRIMARY KEY, school_id uuid NOT NULL REFERENCES public.school,
 partition_key text NOT NULL, event_type text NOT NULL, aggregate_id uuid NOT NULL,
 aggregate_type text NOT NULL, aggregate_version bigint NOT NULL CHECK(aggregate_version>0),
 changeset_id uuid NOT NULL REFERENCES public.changeset, correlation_id uuid NOT NULL,
 idempotency_namespace text NOT NULL, idempotency_key text NOT NULL, payload_hash text NOT NULL,
 payload jsonb NOT NULL, UNIQUE(school_id,aggregate_id,aggregate_version),
 UNIQUE(school_id,idempotency_namespace,idempotency_key,payload_hash));
CREATE TABLE IF NOT EXISTS public.outbox_message (
 outbox_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 changeset_id uuid NOT NULL REFERENCES public.changeset,
 event_id uuid NOT NULL UNIQUE REFERENCES public.domain_event, status text NOT NULL DEFAULT 'pending');
CREATE TABLE IF NOT EXISTS public.canon_activation (
 canon_activation_id uuid PRIMARY KEY, knowledge_version_id uuid NOT NULL REFERENCES public.knowledge_version);

CREATE TABLE IF NOT EXISTS public.asset (
 asset_id uuid PRIMARY KEY, school_id uuid NOT NULL REFERENCES public.school,
 asset_type text NOT NULL, mime_type text NOT NULL, byte_size bigint NOT NULL,
 checksum_algorithm text NOT NULL, checksum_value text NOT NULL, immutable_flag boolean NOT NULL,
 UNIQUE(checksum_algorithm,checksum_value));
CREATE TABLE IF NOT EXISTS public.source_asset (
 source_id uuid NOT NULL REFERENCES public.source, asset_id uuid NOT NULL REFERENCES public.asset,
 relation_type text NOT NULL, PRIMARY KEY(source_id,asset_id,relation_type));
