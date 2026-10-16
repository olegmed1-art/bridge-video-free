\set ON_ERROR_STOP on
-- Read-only probe for a disposable database with migration 0329 installed.
-- No real source, student, rights decision, or Canon activation is involved.
DO $$
DECLARE
    case_row record;
    actual boolean;
BEGIN
    FOR case_row IN
        SELECT * FROM (VALUES
            ('unicode-range-cyrillic', '{"notes":"North has 10-12я"}'::jsonb, true),
            ('unicode-range-accented', '{"notes":"North has 10+é"}'::jsonb, true),
            ('suit-range-spades', '{"notes":"North has 10-12♠"}'::jsonb, true),
            ('suit-range-hearts', '{"notes":"North has 10+♥"}'::jsonb, true),
            ('russian-word-prefix', '{"notes":"Порука партнера: Q — подпись поручителя"}'::jsonb, false),
            ('russian-hidden-hand', '{"notes":"рука партнера: Q"}'::jsonb, true),
            ('english-hidden-hand', '{"notes":"N: AKQJ"}'::jsonb, true),
            ('length-description', '{"notes":"North has 10-12 points"}'::jsonb, false),
            ('plain-range', '{"notes":"North has 10+"}'::jsonb, false)
            ,('range-closing-bracket', '{"notes":"North has 10-12]"}'::jsonb, false)
            ,('range-closing-curly-quote', '{"notes":"North has 10-12”"}'::jsonb, false)
            ,('range-followed-by-latin-letter', '{"notes":"North has 10-12é"}'::jsonb, true)
            ,('range-followed-by-greek-letter', '{"notes":"North has 10-12α"}'::jsonb, true)
            ,('range-followed-by-han-letter', '{"notes":"North has 10-12字"}'::jsonb, true)
            ,('range-followed-by-underscore', '{"notes":"North has 10-12_AKQJ"}'::jsonb, true)
        ) AS examples(label, payload, expected)
    LOOP
        SELECT bidding.contains_forbidden_hidden_value(case_row.payload) INTO actual;
        IF actual IS DISTINCT FROM case_row.expected THEN
            RAISE EXCEPTION '329 firewall mismatch: %, got %, expected %',
                case_row.label, actual, case_row.expected;
        END IF;
    END LOOP;
END $$;
