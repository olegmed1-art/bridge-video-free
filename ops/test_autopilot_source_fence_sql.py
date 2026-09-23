"""PostgreSQL 18 integration proof for the read-only source fence query."""
import os
import unittest

import psycopg

from ops import autopilot_source_fence_probe as probe


@unittest.skipUnless(os.getenv('TEST_POSTGRES_URL'), 'requires isolated PostgreSQL')
class SourceFenceSQL(unittest.TestCase):
    def test_effective_grants_and_public_function_default(self):
        with psycopg.connect(os.environ['TEST_POSTGRES_URL']) as db:
            try:
                with db.cursor() as cur:
                    for principal in probe.PRINCIPALS:
                        cur.execute(f'CREATE ROLE {principal} NOLOGIN')
                    cur.execute('CREATE SCHEMA autopilot')
                    cur.execute('CREATE SCHEMA autopilot_reconcile')
                    cur.execute('CREATE TABLE autopilot.task (id integer)')
                    cur.execute('CREATE SEQUENCE autopilot.task_seq')
                    cur.execute('CREATE FUNCTION autopilot.ping() RETURNS integer LANGUAGE sql AS $$ SELECT 1 $$')
                    cur.execute('REVOKE ALL ON FUNCTION autopilot.ping() FROM PUBLIC')

                    def inspect():
                        result={}
                        for principal in probe.PRINCIPALS:
                            cur.execute(probe.QUERY,(principal,))
                            result[principal]=tuple(cur.fetchone())
                        return probe.assess(result)

                    self.assertTrue(inspect()['application_principals_fenced'])
                    for item in ('SCHEMA autopilot','TABLE autopilot.task',
                                 'SEQUENCE autopilot.task_seq','FUNCTION autopilot.ping()'):
                        privilege={'SCHEMA':'USAGE','TABLE':'SELECT','SEQUENCE':'USAGE',
                                   'FUNCTION':'EXECUTE'}[item.split()[0]]
                        cur.execute(f'GRANT {privilege} ON {item} TO {probe.PRINCIPALS[0]}')
                        self.assertFalse(inspect()['application_principals_fenced'],item)
                        cur.execute(f'REVOKE {privilege} ON {item} FROM {probe.PRINCIPALS[0]}')
                    cur.execute('GRANT EXECUTE ON FUNCTION autopilot.ping() TO PUBLIC')
                    self.assertFalse(inspect()['application_principals_fenced'])
            finally:
                db.rollback()


if __name__=='__main__':
    unittest.main()
