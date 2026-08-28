-- 0200_bidding_knowledge_v0 / part 01
-- Included transactionally by ../0200_bidding_knowledge_v0.sql.

CREATE SCHEMA IF NOT EXISTS bidding;

COMMENT ON SCHEMA bidding IS
  'Executable bidding knowledge, test evidence, activation, ingestion audit and decision traces. SCHOOL CANON and WORLD remain authority-separated.';

REVOKE ALL ON SCHEMA bidding FROM PUBLIC;

GRANT USAGE ON SCHEMA bidding TO bridge_school_reader, bridge_school_app, bridge_school_worker;

ALTER DEFAULT PRIVILEGES IN SCHEMA bidding REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

CREATE OR REPLACE FUNCTION bidding.contains_forbidden_hidden_key(payload jsonb)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
WITH RECURSIVE walk(value,key_path) AS (
    SELECT COALESCE(payload, 'null'::jsonb), ARRAY[]::text[]
    UNION ALL
    SELECT
        child.value,
        CASE
            WHEN cardinality(child.key_tokens)=0 THEN w.key_path
            ELSE w.key_path || child.key_tokens
        END
      FROM walk AS w
      CROSS JOIN LATERAL (
          SELECT
              e.value,
              array_remove(
                  regexp_split_to_array(
                      lower(
                          regexp_replace(
                              e.key,
                              '([a-z0-9])([A-Z])',
                              E'\\1_\\2',
                              'g'
                          )
                      ),
                      '[^a-z0-9]+'
                  ),
                  ''
              ) AS key_tokens
            FROM jsonb_each(
                CASE WHEN jsonb_typeof(w.value)='object' THEN w.value ELSE '{}'::jsonb END
            ) AS e
          UNION ALL
          SELECT
              a.value,
              ARRAY[]::text[] AS key_tokens
            FROM jsonb_array_elements(
                CASE WHEN jsonb_typeof(w.value)='array' THEN w.value ELSE '[]'::jsonb END
            ) AS a
      ) AS child
), forbidden_alias(alias) AS (
    SELECT unnest(ARRAY[
        'partnerhand','partnerhands','opponenthand','opponenthands',
        'otherhand','otherhands','handother','handsother',
        'handpartner','handspartner','handopponent','handsopponent',
        'cardpartner','cardopponent','cardother',
        'cardspartner','cardsopponent','cardsother',
        'partnercard','opponentcard','othercard',
        'partnercards','opponentcards','othercards',
        'northhand','easthand','southhand','westhand',
        'handnorth','handeast','handsouth','handwest',
        'handsnorth','handseast','handssouth','handswest',
        'northcard','eastcard','southcard','westcard',
        'cardnorth','cardeast','cardsouth','cardwest',
        'northcards','eastcards','southcards','westcards',
        'cardsnorth','cardseast','cardssouth','cardswest',
        'handn','hande','handw',
        'handsn','handse','handss','handsw',
        'nhand','ehand','shand','whand',
        'nhands','ehands','shands','whands',
        'ncard','ecard','scard','wcard',
        'cardn','carde','cardw',
        'ncards','ecards','scards','wcards',
        'cardsn','cardse','cardss','cardsw',
        'fulldeal','dealfull','hiddencard','cardhidden',
        'hiddencards','cardshidden',
        'actualpartnerhand','actualopponenthand','actualopponenthands'
    ])
), metric_word(word) AS (
    SELECT unnest(ARRAY[
        'played','count','counts','total','totals',
        'rate','rates','average','averages','avg',
        'percentage','percentages','pct'
    ])
), sensitive_suffix(word) AS (
    SELECT unnest(ARRAY[
        'hcp','point','points','shape','distribution',
        'length','lengths','holding','holdings','honor','honors',
        'control','controls','spade','spades','heart','hearts',
        'diamond','diamonds','club','clubs','card','cards',
        'hand','hands','deal'
    ])
), allowed_suffix(word) AS (
    SELECT word FROM metric_word
    UNION ALL
    SELECT word FROM sensitive_suffix
), forbidden AS (
    SELECT 1
      FROM walk AS w
     WHERE EXISTS (
        SELECT 1
          FROM generate_subscripts(w.key_path,1) AS path_start(i)
          CROSS JOIN generate_subscripts(w.key_path,1) AS path_end(j)
         WHERE path_end.j >= path_start.i
           AND EXISTS (
                SELECT 1
                 FROM forbidden_alias AS f
                 WHERE (
                       array_to_string(
                           w.key_path[path_start.i:path_end.j],''
                       ) = f.alias
                       OR (
                           array_to_string(
                               w.key_path[path_start.i:path_end.j],''
                           ) LIKE f.alias || '%'
                           AND substring(
                               array_to_string(
                                   w.key_path[path_start.i:path_end.j],''
                               ) FROM length(f.alias)+1
                           ) ~ (
                               SELECT '^(' || string_agg(word,'|') || ')+$'
                                 FROM allowed_suffix
                           )
                       )
                 )
                   AND NOT (
                       jsonb_typeof(w.value)='number'
                       AND (
                           (
                               array_to_string(
                                   w.key_path[path_start.i:path_end.j],''
                               ) = f.alias
                               AND EXISTS (
                                   SELECT 1
                                     FROM metric_word AS metric
                                    WHERE path_end.j < cardinality(w.key_path)
                                      AND w.key_path[path_end.j+1]=metric.word
                               )
                           )
                           OR EXISTS (
                               SELECT 1
                                 FROM metric_word AS metric
                                WHERE array_to_string(
                                          w.key_path[path_start.i:path_end.j],''
                                      ) = f.alias || metric.word
                           )
                        )
                   )
           )
     )
        OR EXISTS (
            SELECT 1
              FROM generate_subscripts(w.key_path,1) AS left_pos(i)
              CROSS JOIN generate_subscripts(w.key_path,1) AS right_pos(j)
             WHERE left_pos.i < right_pos.j
               AND NOT (
                    jsonb_typeof(w.value)='number'
                    AND EXISTS (
                        SELECT 1
                          FROM metric_word AS metric
                         WHERE (
                             w.key_path[left_pos.i] IN (
                                 'hand','hands','card','cards'
                             )
                             AND left_pos.i < cardinality(w.key_path)
                             AND w.key_path[left_pos.i+1]=metric.word
                         )
                            OR (
                                w.key_path[right_pos.j] IN (
                                    'hand','hands','card','cards'
                                )
                                AND right_pos.j < cardinality(w.key_path)
                                AND w.key_path[right_pos.j+1]=metric.word
                            )
                    )
               )
               AND (
                    (
                        w.key_path[left_pos.i] IN ('partner','opponent','opponents','other','others')
                        AND w.key_path[right_pos.j] IN ('hand','hands','card','cards')
                    )
                    OR (
                        w.key_path[left_pos.i] IN ('hand','hands','card','cards')
                        AND w.key_path[right_pos.j] IN (
                            'partner','opponent','opponents','other','others'
                        )
                    )
                    OR (
                        w.key_path[left_pos.i] IN (
                            'north','east','south','west','n','e','s','w'
                        )
                        AND w.key_path[right_pos.j] IN ('hand','hands','card','cards')
                    )
                    OR (
                        w.key_path[left_pos.i] IN ('hand','hands','card','cards')
                        AND w.key_path[right_pos.j] IN (
                            'north','east','south','west','n','e','s','w'
                        )
                    )
                    OR (
                        w.key_path[left_pos.i]='full'
                        AND w.key_path[right_pos.j]='deal'
                    )
                    OR (
                        w.key_path[left_pos.i]='hidden'
                        AND w.key_path[right_pos.j] IN ('card','cards')
                    )
                    OR (
                        w.key_path[left_pos.i] IN ('card','cards')
                        AND w.key_path[right_pos.j]='hidden'
                    )
                    OR (
                        w.key_path[left_pos.i]='deal'
                        AND w.key_path[right_pos.j]='full'
                    )
               )
        )
        OR (
            jsonb_typeof(w.value) <> 'number'
            AND (
                'allhands'=ANY(w.key_path)
                OR EXISTS (
                    SELECT 1
                      FROM unnest(w.key_path) AS compact(segment)
                      CROSS JOIN metric_word AS metric
                     WHERE compact.segment='allhands' || metric.word
                )
                OR EXISTS (
                    SELECT 1
                      FROM generate_subscripts(w.key_path,1) AS all_pos(i)
                      CROSS JOIN generate_subscripts(w.key_path,1) AS hand_pos(j)
                     WHERE (
                         w.key_path[all_pos.i]='all'
                         AND w.key_path[hand_pos.j] IN ('hand','hands')
                     )
                       AND all_pos.i <> hand_pos.j
                )
            )
        )
        OR (
            jsonb_typeof(w.value)='object'
            AND EXISTS (
                SELECT 1
                  FROM jsonb_each(w.value) AS seat_field(key,value)
                 WHERE jsonb_typeof(seat_field.value)='string'
                   AND (
                       (
                           regexp_replace(lower(seat_field.key),'[^a-z0-9]','','g')='owner'
                           AND lower(seat_field.value #>> '{}') IN (
                               'partner','opponent','opponents','other','others'
                           )
                       )
                       OR (
                           (
                               'hand'=ANY(w.key_path)
                               OR 'hands'=ANY(w.key_path)
                               OR 'allhands'=ANY(w.key_path)
                           )
                           AND regexp_replace(
                               lower(seat_field.key),'[^a-z0-9]','','g'
                           ) IN ('seat','owner')
                           AND upper(seat_field.value #>> '{}') IN (
                               'N','E','S','W','NORTH','EAST','SOUTH','WEST'
                           )
                       )
                   )
            )
            AND EXISTS (
                SELECT 1
                  FROM jsonb_each(w.value) AS cards_field(key,value)
                 WHERE regexp_replace(lower(cards_field.key),'[^a-z0-9]','','g')
                       IN ('card','cards')
                   AND jsonb_typeof(cards_field.value) <> 'null'
            )
        )
     LIMIT 1
)
SELECT EXISTS (SELECT 1 FROM forbidden);
$$;

CREATE TABLE bidding.rule (
    rule_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES public.school(school_id) ON DELETE RESTRICT,
    knowledge_version_id uuid NOT NULL UNIQUE
        REFERENCES public.knowledge_version(knowledge_version_id) ON DELETE RESTRICT,
    rule_key text NOT NULL CHECK (btrim(rule_key) <> ''),
    rule_kind text NOT NULL
        CHECK (rule_kind IN ('bid','inference','priority','exception','fallback')),
    auction_pattern jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(auction_pattern)='object'),
    hand_constraints jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(hand_constraints)='object'),
    public_context_constraints jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(public_context_constraints)='object'),
    action jsonb NOT NULL CHECK (jsonb_typeof(action)='object'),
    meaning jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(meaning)='object'),
    public_inference jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(public_inference)='object'),
    alert_semantics jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(alert_semantics)='object'),
    forcing_semantics jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(forcing_semantics)='object'),
    priority integer NOT NULL DEFAULT 0,
    specificity integer NOT NULL DEFAULT 0 CHECK (specificity >= 0),
    explanation jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(explanation)='object'),
    condition_schema_version text NOT NULL DEFAULT 'bidding-condition-v0',
    compiled_payload jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(compiled_payload)='object'),
    lifecycle_status text NOT NULL DEFAULT 'candidate'
        CHECK (lifecycle_status IN ('candidate','validated','retired')),
    method_version text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (updated_at >= created_at),
    CHECK (NOT bidding.contains_forbidden_hidden_key(auction_pattern)),
    CHECK (NOT bidding.contains_forbidden_hidden_key(hand_constraints)),
    CHECK (NOT bidding.contains_forbidden_hidden_key(public_context_constraints)),
    CHECK (NOT bidding.contains_forbidden_hidden_key(action)),
    CHECK (NOT bidding.contains_forbidden_hidden_key(meaning)),
    CHECK (NOT bidding.contains_forbidden_hidden_key(public_inference)),
    CHECK (NOT bidding.contains_forbidden_hidden_key(alert_semantics)),
    CHECK (NOT bidding.contains_forbidden_hidden_key(forcing_semantics)),
    CHECK (NOT bidding.contains_forbidden_hidden_key(explanation)),
    CHECK (NOT bidding.contains_forbidden_hidden_key(compiled_payload)),
    UNIQUE (school_id, rule_key)
);

CREATE INDEX bidding_rule_runtime_lookup_idx
    ON bidding.rule (school_id, lifecycle_status, priority DESC, specificity DESC, rule_key);

CREATE OR REPLACE FUNCTION bidding.validate_rule_school_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_school_id uuid;
BEGIN
    SELECT ki.school_id
      INTO v_school_id
      FROM public.knowledge_version AS kv
      JOIN public.knowledge_item AS ki
        ON ki.knowledge_item_id=kv.knowledge_item_id
     WHERE kv.knowledge_version_id=NEW.knowledge_version_id;
    IF v_school_id IS NULL OR v_school_id <> NEW.school_id THEN
        RAISE EXCEPTION 'BID_RULE_KNOWLEDGE_SCHOOL_MISMATCH' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER rule_school_scope_guard
BEFORE INSERT OR UPDATE OF school_id, knowledge_version_id ON bidding.rule
FOR EACH ROW EXECUTE FUNCTION bidding.validate_rule_school_scope();

CREATE TABLE bidding.rule_relation (
    rule_relation_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES public.school(school_id) ON DELETE RESTRICT,
    from_rule_id uuid NOT NULL REFERENCES bidding.rule(rule_id) ON DELETE RESTRICT,
    to_rule_id uuid NOT NULL REFERENCES bidding.rule(rule_id) ON DELETE RESTRICT,
    relation_type text NOT NULL
        CHECK (relation_type IN ('depends_on','overrides','excludes','continues_to','implies')),
    conditions jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(conditions)='object'),
    method_version text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (from_rule_id <> to_rule_id),
    CHECK (NOT bidding.contains_forbidden_hidden_key(conditions)),
    UNIQUE (from_rule_id, to_rule_id, relation_type)
);

CREATE INDEX bidding_rule_relation_to_idx
    ON bidding.rule_relation (school_id, to_rule_id, relation_type);

CREATE OR REPLACE FUNCTION bidding.validate_rule_relation_school_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_from_school uuid;
    v_to_school uuid;
BEGIN
    SELECT school_id INTO v_from_school FROM bidding.rule WHERE rule_id=NEW.from_rule_id;
    SELECT school_id INTO v_to_school FROM bidding.rule WHERE rule_id=NEW.to_rule_id;
    IF v_from_school IS NULL OR v_to_school IS NULL
       OR v_from_school <> NEW.school_id OR v_to_school <> NEW.school_id THEN
        RAISE EXCEPTION 'BID_RULE_RELATION_SCHOOL_MISMATCH' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER rule_relation_school_scope_guard
BEFORE INSERT OR UPDATE OF school_id, from_rule_id, to_rule_id ON bidding.rule_relation
FOR EACH ROW EXECUTE FUNCTION bidding.validate_rule_relation_school_scope();

CREATE TABLE bidding.rule_test (
    rule_test_id uuid PRIMARY KEY DEFAULT uuidv7(),
    school_id uuid NOT NULL REFERENCES public.school(school_id) ON DELETE RESTRICT,
    rule_id uuid NOT NULL REFERENCES bidding.rule(rule_id) ON DELETE RESTRICT,
    test_key text NOT NULL CHECK (btrim(test_key) <> ''),
    test_type text NOT NULL CHECK (test_type IN (
        'positive','negative','boundary','interference',
        'hidden_information','conflict','regression'
    )),
    fixture jsonb NOT NULL CHECK (jsonb_typeof(fixture)='object'),
    expected jsonb NOT NULL CHECK (jsonb_typeof(expected)='object'),
    enabled boolean NOT NULL DEFAULT true,
    method_version text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (test_type='hidden_information' OR NOT bidding.contains_forbidden_hidden_key(fixture)),
    CHECK (NOT bidding.contains_forbidden_hidden_key(expected)),
    UNIQUE (rule_id, test_key)
);
