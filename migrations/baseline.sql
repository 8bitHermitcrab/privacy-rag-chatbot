--
-- PostgreSQL database dump
--


-- Dumped from database version 16.15 (Debian 16.15-1.pgdg12+2)
-- Dumped by pg_dump version 16.15 (Debian 16.15-1.pgdg12+2)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: auth; Type: SCHEMA; Schema: -; Owner: admin
--

CREATE SCHEMA auth;


ALTER SCHEMA auth OWNER TO migration_user;

--
-- Name: rag; Type: SCHEMA; Schema: -; Owner: rag_user
--

CREATE SCHEMA rag;


ALTER SCHEMA rag OWNER TO rag_user;

--
-- Name: vault; Type: SCHEMA; Schema: -; Owner: vault_user
--

CREATE SCHEMA vault;


ALTER SCHEMA vault OWNER TO vault_user;

--
-- Name: vector; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;


--
-- Name: set_updated_at(); Type: FUNCTION; Schema: auth; Owner: admin
--

CREATE FUNCTION auth.set_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;


ALTER FUNCTION auth.set_updated_at() OWNER TO migration_user;

--
-- Name: set_person_updated_at(); Type: FUNCTION; Schema: vault; Owner: vault_user
--

CREATE FUNCTION vault.set_person_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;


ALTER FUNCTION vault.set_person_updated_at() OWNER TO vault_user;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: employee_info; Type: TABLE; Schema: auth; Owner: admin
--

CREATE TABLE auth.employee_info (
    employee_id text NOT NULL,
    name_token text,
    team_id text,
    position_code text,
    verification_status text DEFAULT 'PENDING'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT employee_info_check CHECK (((verification_status <> 'CONFIRMED'::text) OR ((team_id IS NOT NULL) AND (name_token IS NOT NULL)))),
    CONSTRAINT employee_info_employee_id_check CHECK ((btrim(employee_id) <> ''::text)),
    CONSTRAINT employee_info_verification_status_check CHECK ((verification_status = ANY (ARRAY['PENDING'::text, 'CONFIRMED'::text, 'REVIEW_REQUIRED'::text])))
);


ALTER TABLE auth.employee_info OWNER TO migration_user;

--
-- Name: permission_change_log; Type: TABLE; Schema: auth; Owner: admin
--

CREATE TABLE auth.permission_change_log (
    log_id bigint NOT NULL,
    actor_user_id bigint NOT NULL,
    target_user_id bigint,
    target_role_code text,
    action text NOT NULL,
    before_value jsonb,
    after_value jsonb,
    reason text NOT NULL,
    changed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT permission_change_log_action_check CHECK ((btrim(action) <> ''::text)),
    CONSTRAINT permission_change_log_after_value_check CHECK (((after_value IS NULL) OR (jsonb_typeof(after_value) = 'object'::text))),
    CONSTRAINT permission_change_log_after_value_check1 CHECK (((after_value IS NULL) OR (NOT (after_value ?| ARRAY['password'::text, 'pw'::text, 'password_hash'::text, 'hmac_key'::text, 'api_key'::text])))),
    CONSTRAINT permission_change_log_before_value_check CHECK (((before_value IS NULL) OR (jsonb_typeof(before_value) = 'object'::text))),
    CONSTRAINT permission_change_log_before_value_check1 CHECK (((before_value IS NULL) OR (NOT (before_value ?| ARRAY['password'::text, 'pw'::text, 'password_hash'::text, 'hmac_key'::text, 'api_key'::text])))),
    CONSTRAINT permission_change_log_check CHECK (((target_user_id IS NOT NULL) OR (target_role_code IS NOT NULL))),
    CONSTRAINT permission_change_log_reason_check CHECK ((btrim(reason) <> ''::text))
);


ALTER TABLE auth.permission_change_log OWNER TO migration_user;

--
-- Name: permission_change_log_log_id_seq; Type: SEQUENCE; Schema: auth; Owner: admin
--

ALTER TABLE auth.permission_change_log ALTER COLUMN log_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME auth.permission_change_log_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: role_info; Type: TABLE; Schema: auth; Owner: admin
--

CREATE TABLE auth.role_info (
    role_code text NOT NULL,
    role_name text NOT NULL,
    search_scope text NOT NULL,
    can_restore boolean DEFAULT false NOT NULL,
    can_manage_users boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT role_info_check CHECK (((NOT can_restore) OR (search_scope <> 'NONE'::text))),
    CONSTRAINT role_info_role_code_check CHECK ((role_code = ANY (ARRAY['STAFF'::text, 'TEAM_LEAD'::text, 'ADMIN'::text]))),
    CONSTRAINT role_info_role_name_check CHECK ((btrim(role_name) <> ''::text)),
    CONSTRAINT role_info_search_scope_check CHECK ((search_scope = ANY (ARRAY['OWN'::text, 'TEAM'::text, 'NONE'::text])))
);


ALTER TABLE auth.role_info OWNER TO migration_user;

--
-- Name: team_info; Type: TABLE; Schema: auth; Owner: admin
--

CREATE TABLE auth.team_info (
    team_id text NOT NULL,
    team_name text NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT team_info_team_id_check CHECK ((btrim(team_id) <> ''::text)),
    CONSTRAINT team_info_team_name_check CHECK ((btrim(team_name) <> ''::text))
);


ALTER TABLE auth.team_info OWNER TO migration_user;

--
-- Name: user_info; Type: TABLE; Schema: auth; Owner: admin
--

CREATE TABLE auth.user_info (
    user_id bigint NOT NULL,
    login_id text NOT NULL,
    password_hash text NOT NULL,
    employee_id text,
    role_code text NOT NULL,
    status text DEFAULT 'ACTIVE'::text NOT NULL,
    must_change_password boolean DEFAULT true NOT NULL,
    last_login_at timestamp with time zone,
    password_changed_at timestamp with time zone,
    user_dfn_01 text,
    user_dfn_02 text,
    user_dfn_03 text,
    user_dfn_04 text,
    user_dfn_05 text,
    created_by bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by bigint,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT user_info_check CHECK (((role_code = 'ADMIN'::text) OR (employee_id IS NOT NULL))),
    CONSTRAINT user_info_login_id_check CHECK ((btrim(login_id) <> ''::text)),
    CONSTRAINT user_info_password_hash_check CHECK ((btrim(password_hash) <> ''::text)),
    CONSTRAINT user_info_status_check CHECK ((status = ANY (ARRAY['ACTIVE'::text, 'DORMANT'::text, 'LOCKED'::text, 'RETIRED'::text])))
);


ALTER TABLE auth.user_info OWNER TO migration_user;

--
-- Name: user_info_user_id_seq; Type: SEQUENCE; Schema: auth; Owner: admin
--

ALTER TABLE auth.user_info ALTER COLUMN user_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME auth.user_info_user_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: chunks; Type: TABLE; Schema: rag; Owner: rag_user
--

CREATE TABLE rag.chunks (
    chunk_id text NOT NULL,
    doc_id text NOT NULL,
    chunk_idx integer NOT NULL,
    doc_type text,
    masked_text text NOT NULL,
    tokens text[] DEFAULT '{}'::text[] NOT NULL,
    embedding public.vector(1024) NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT ck_chunks_index_nonnegative CHECK ((chunk_idx >= 0))
);


ALTER TABLE rag.chunks OWNER TO rag_user;

--
-- Name: document_info; Type: TABLE; Schema: rag; Owner: rag_user
--

CREATE TABLE rag.document_info (
    document_id text NOT NULL,
    document_type text,
    owner_employee_id text,
    team_id text,
    metadata_status text DEFAULT 'PENDING'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT document_info_check CHECK (((metadata_status <> 'CONFIRMED'::text) OR ((owner_employee_id IS NOT NULL) AND (team_id IS NOT NULL)))),
    CONSTRAINT document_info_document_id_check CHECK ((btrim(document_id) <> ''::text)),
    CONSTRAINT document_info_metadata_status_check CHECK ((metadata_status = ANY (ARRAY['PENDING'::text, 'CONFIRMED'::text, 'REVIEW_REQUIRED'::text])))
);


ALTER TABLE rag.document_info OWNER TO rag_user;

--
-- Name: person; Type: TABLE; Schema: vault; Owner: vault_user
--

CREATE TABLE vault.person (
    pid bigint NOT NULL,
    data1 text[],
    data2 text[],
    data3 text[],
    data4 text[],
    data5 text[],
    data6 text[],
    data7 text[],
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_person_has_at_least_one_token CHECK ((((((((COALESCE(cardinality(array_remove(data1, NULL::text)), 0) + COALESCE(cardinality(array_remove(data2, NULL::text)), 0)) + COALESCE(cardinality(array_remove(data3, NULL::text)), 0)) + COALESCE(cardinality(array_remove(data4, NULL::text)), 0)) + COALESCE(cardinality(array_remove(data5, NULL::text)), 0)) + COALESCE(cardinality(array_remove(data6, NULL::text)), 0)) + COALESCE(cardinality(array_remove(data7, NULL::text)), 0)) > 0))
);


ALTER TABLE vault.person OWNER TO vault_user;

--
-- Name: person_pid_seq; Type: SEQUENCE; Schema: vault; Owner: vault_user
--

ALTER TABLE vault.person ALTER COLUMN pid ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME vault.person_pid_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: token_map; Type: TABLE; Schema: vault; Owner: vault_user
--

CREATE TABLE vault.token_map (
    token text NOT NULL,
    label text NOT NULL,
    normalized text NOT NULL,
    originals text[] DEFAULT '{}'::text[] NOT NULL,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE vault.token_map OWNER TO vault_user;

--
-- Name: employee_info employee_info_pkey; Type: CONSTRAINT; Schema: auth; Owner: admin
--

ALTER TABLE ONLY auth.employee_info
    ADD CONSTRAINT employee_info_pkey PRIMARY KEY (employee_id);


--
-- Name: permission_change_log permission_change_log_pkey; Type: CONSTRAINT; Schema: auth; Owner: admin
--

ALTER TABLE ONLY auth.permission_change_log
    ADD CONSTRAINT permission_change_log_pkey PRIMARY KEY (log_id);


--
-- Name: role_info role_info_pkey; Type: CONSTRAINT; Schema: auth; Owner: admin
--

ALTER TABLE ONLY auth.role_info
    ADD CONSTRAINT role_info_pkey PRIMARY KEY (role_code);


--
-- Name: team_info team_info_pkey; Type: CONSTRAINT; Schema: auth; Owner: admin
--

ALTER TABLE ONLY auth.team_info
    ADD CONSTRAINT team_info_pkey PRIMARY KEY (team_id);


--
-- Name: team_info team_info_team_name_key; Type: CONSTRAINT; Schema: auth; Owner: admin
--

ALTER TABLE ONLY auth.team_info
    ADD CONSTRAINT team_info_team_name_key UNIQUE (team_name);


--
-- Name: user_info user_info_employee_id_key; Type: CONSTRAINT; Schema: auth; Owner: admin
--

ALTER TABLE ONLY auth.user_info
    ADD CONSTRAINT user_info_employee_id_key UNIQUE (employee_id);


--
-- Name: user_info user_info_login_id_key; Type: CONSTRAINT; Schema: auth; Owner: admin
--

ALTER TABLE ONLY auth.user_info
    ADD CONSTRAINT user_info_login_id_key UNIQUE (login_id);


--
-- Name: user_info user_info_pkey; Type: CONSTRAINT; Schema: auth; Owner: admin
--

ALTER TABLE ONLY auth.user_info
    ADD CONSTRAINT user_info_pkey PRIMARY KEY (user_id);


--
-- Name: chunks chunks_pkey; Type: CONSTRAINT; Schema: rag; Owner: rag_user
--

ALTER TABLE ONLY rag.chunks
    ADD CONSTRAINT chunks_pkey PRIMARY KEY (chunk_id);


--
-- Name: document_info document_info_pkey; Type: CONSTRAINT; Schema: rag; Owner: rag_user
--

ALTER TABLE ONLY rag.document_info
    ADD CONSTRAINT document_info_pkey PRIMARY KEY (document_id);


--
-- Name: chunks uq_chunks_document_index; Type: CONSTRAINT; Schema: rag; Owner: rag_user
--

ALTER TABLE ONLY rag.chunks
    ADD CONSTRAINT uq_chunks_document_index UNIQUE (doc_id, chunk_idx);


--
-- Name: person person_pkey; Type: CONSTRAINT; Schema: vault; Owner: vault_user
--

ALTER TABLE ONLY vault.person
    ADD CONSTRAINT person_pkey PRIMARY KEY (pid);


--
-- Name: token_map token_map_pkey; Type: CONSTRAINT; Schema: vault; Owner: vault_user
--

ALTER TABLE ONLY vault.token_map
    ADD CONSTRAINT token_map_pkey PRIMARY KEY (token);


--
-- Name: ix_employee_team; Type: INDEX; Schema: auth; Owner: admin
--

CREATE INDEX ix_employee_team ON auth.employee_info USING btree (team_id);


--
-- Name: ix_permission_log_actor_time; Type: INDEX; Schema: auth; Owner: admin
--

CREATE INDEX ix_permission_log_actor_time ON auth.permission_change_log USING btree (actor_user_id, changed_at);


--
-- Name: ix_permission_log_target_time; Type: INDEX; Schema: auth; Owner: admin
--

CREATE INDEX ix_permission_log_target_time ON auth.permission_change_log USING btree (target_user_id, changed_at);


--
-- Name: ix_user_role; Type: INDEX; Schema: auth; Owner: admin
--

CREATE INDEX ix_user_role ON auth.user_info USING btree (role_code);


--
-- Name: chunks_doc_id; Type: INDEX; Schema: rag; Owner: rag_user
--

CREATE INDEX chunks_doc_id ON rag.chunks USING btree (doc_id);


--
-- Name: chunks_embedding_hnsw; Type: INDEX; Schema: rag; Owner: rag_user
--

CREATE INDEX chunks_embedding_hnsw ON rag.chunks USING hnsw (embedding public.vector_cosine_ops);


--
-- Name: chunks_tokens_gin; Type: INDEX; Schema: rag; Owner: rag_user
--

CREATE INDEX chunks_tokens_gin ON rag.chunks USING gin (tokens);


--
-- Name: ix_document_owner_confirmed; Type: INDEX; Schema: rag; Owner: rag_user
--

CREATE INDEX ix_document_owner_confirmed ON rag.document_info USING btree (owner_employee_id) WHERE (metadata_status = 'CONFIRMED'::text);


--
-- Name: ix_document_team_confirmed; Type: INDEX; Schema: rag; Owner: rag_user
--

CREATE INDEX ix_document_team_confirmed ON rag.document_info USING btree (team_id) WHERE (metadata_status = 'CONFIRMED'::text);


--
-- Name: employee_info trg_set_updated_at; Type: TRIGGER; Schema: auth; Owner: admin
--

CREATE TRIGGER trg_set_updated_at BEFORE UPDATE ON auth.employee_info FOR EACH ROW EXECUTE FUNCTION auth.set_updated_at();


--
-- Name: role_info trg_set_updated_at; Type: TRIGGER; Schema: auth; Owner: admin
--

CREATE TRIGGER trg_set_updated_at BEFORE UPDATE ON auth.role_info FOR EACH ROW EXECUTE FUNCTION auth.set_updated_at();


--
-- Name: team_info trg_set_updated_at; Type: TRIGGER; Schema: auth; Owner: admin
--

CREATE TRIGGER trg_set_updated_at BEFORE UPDATE ON auth.team_info FOR EACH ROW EXECUTE FUNCTION auth.set_updated_at();


--
-- Name: user_info trg_set_updated_at; Type: TRIGGER; Schema: auth; Owner: admin
--

CREATE TRIGGER trg_set_updated_at BEFORE UPDATE ON auth.user_info FOR EACH ROW EXECUTE FUNCTION auth.set_updated_at();


--
-- Name: document_info trg_set_updated_at; Type: TRIGGER; Schema: rag; Owner: rag_user
--

CREATE TRIGGER trg_set_updated_at BEFORE UPDATE ON rag.document_info FOR EACH ROW EXECUTE FUNCTION auth.set_updated_at();


--
-- Name: person trg_person_set_updated_at; Type: TRIGGER; Schema: vault; Owner: vault_user
--

CREATE TRIGGER trg_person_set_updated_at BEFORE UPDATE ON vault.person FOR EACH ROW EXECUTE FUNCTION vault.set_person_updated_at();


--
-- Name: employee_info employee_info_team_id_fkey; Type: FK CONSTRAINT; Schema: auth; Owner: admin
--

ALTER TABLE ONLY auth.employee_info
    ADD CONSTRAINT employee_info_team_id_fkey FOREIGN KEY (team_id) REFERENCES auth.team_info(team_id);


--
-- Name: permission_change_log permission_change_log_actor_user_id_fkey; Type: FK CONSTRAINT; Schema: auth; Owner: admin
--

ALTER TABLE ONLY auth.permission_change_log
    ADD CONSTRAINT permission_change_log_actor_user_id_fkey FOREIGN KEY (actor_user_id) REFERENCES auth.user_info(user_id);


--
-- Name: permission_change_log permission_change_log_target_role_code_fkey; Type: FK CONSTRAINT; Schema: auth; Owner: admin
--

ALTER TABLE ONLY auth.permission_change_log
    ADD CONSTRAINT permission_change_log_target_role_code_fkey FOREIGN KEY (target_role_code) REFERENCES auth.role_info(role_code);


--
-- Name: permission_change_log permission_change_log_target_user_id_fkey; Type: FK CONSTRAINT; Schema: auth; Owner: admin
--

ALTER TABLE ONLY auth.permission_change_log
    ADD CONSTRAINT permission_change_log_target_user_id_fkey FOREIGN KEY (target_user_id) REFERENCES auth.user_info(user_id);


--
-- Name: user_info user_info_created_by_fkey; Type: FK CONSTRAINT; Schema: auth; Owner: admin
--

ALTER TABLE ONLY auth.user_info
    ADD CONSTRAINT user_info_created_by_fkey FOREIGN KEY (created_by) REFERENCES auth.user_info(user_id);


--
-- Name: user_info user_info_employee_id_fkey; Type: FK CONSTRAINT; Schema: auth; Owner: admin
--

ALTER TABLE ONLY auth.user_info
    ADD CONSTRAINT user_info_employee_id_fkey FOREIGN KEY (employee_id) REFERENCES auth.employee_info(employee_id);


--
-- Name: user_info user_info_role_code_fkey; Type: FK CONSTRAINT; Schema: auth; Owner: admin
--

ALTER TABLE ONLY auth.user_info
    ADD CONSTRAINT user_info_role_code_fkey FOREIGN KEY (role_code) REFERENCES auth.role_info(role_code);


--
-- Name: user_info user_info_updated_by_fkey; Type: FK CONSTRAINT; Schema: auth; Owner: admin
--

ALTER TABLE ONLY auth.user_info
    ADD CONSTRAINT user_info_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES auth.user_info(user_id);


--
-- Name: document_info document_info_owner_employee_id_fkey; Type: FK CONSTRAINT; Schema: rag; Owner: rag_user
--

ALTER TABLE ONLY rag.document_info
    ADD CONSTRAINT document_info_owner_employee_id_fkey FOREIGN KEY (owner_employee_id) REFERENCES auth.employee_info(employee_id);


--
-- Name: document_info document_info_team_id_fkey; Type: FK CONSTRAINT; Schema: rag; Owner: rag_user
--

ALTER TABLE ONLY rag.document_info
    ADD CONSTRAINT document_info_team_id_fkey FOREIGN KEY (team_id) REFERENCES auth.team_info(team_id);


--
-- Name: chunks fk_chunks_document_info; Type: FK CONSTRAINT; Schema: rag; Owner: rag_user
--

ALTER TABLE ONLY rag.chunks
    ADD CONSTRAINT fk_chunks_document_info FOREIGN KEY (doc_id) REFERENCES rag.document_info(document_id);


--
-- PostgreSQL database dump complete
--



REVOKE ALL ON SCHEMA vault FROM PUBLIC, rag_user;
REVOKE ALL ON SCHEMA rag FROM PUBLIC, vault_user;
