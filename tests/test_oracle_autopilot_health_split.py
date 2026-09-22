import contextlib
import io
import os
import unittest
from unittest.mock import MagicMock,patch
from database import runtime_health_preflight as target

NEON = ('postgresql://bridge_school_health_principal:synthetic-password@'
        'ep-noisy-pine-b1pe30sf.c-5.eu-central-1.aws.neon.tech/neondb'
        '?sslmode=require&channel_binding=require')
ORACLE = ('postgresql://bridge_school_health_principal:synthetic-password@127.0.0.1:55432/autopilot'
          '?sslmode=verify-full&channel_binding=require&sslrootcert=/tmp/ca.crt')


def connection(rows):
    conn=MagicMock()
    conn.__enter__.return_value=conn
    cur=conn.cursor.return_value.__enter__.return_value
    cur.fetchone.side_effect=rows
    cur.fetchall.return_value=[('mailbox','ok'),('leases','ok'),('dispatch','warning')]
    return conn,cur


class SharedHealthSplit(unittest.TestCase):
    def env(self,backend='postgresql'):
        return {'BRIDGE_HEALTH_DATABASE_URL':NEON,'AUTOPILOT_HEALTH_DATABASE_URL':ORACLE,
                'AUTOPILOT_DB_BACKEND':backend,'AUTOPILOT_PG_DATABASE':'autopilot',
                'AUTOPILOT_PG_HOST':'127.0.0.1','AUTOPILOT_PG_PORT':'55432'}

    def test_school_checks_stay_neon_autopilot_checks_move(self):
        neon,ncur=connection([('bridge_school_health_principal',True,True,True,False,False,False,False),('ok',0,0,1)])
        oracle,ocur=connection([('bridge_school_health_principal','autopilot',True,True,*([False]*7)),('autopilot_operational_health_signal',)])
        with patch.dict(os.environ,self.env(),clear=True),patch.object(target.psycopg,'connect',side_effect=[neon,oracle]) as connect,contextlib.redirect_stdout(io.StringIO()):
            target.main()
        self.assertEqual([c.args[0] for c in connect.call_args_list],[NEON,ORACLE])
        self.assertTrue(oracle.read_only)
        self.assertFalse(any('autopilot_operational_health_signal' in c.args[0] for c in ncur.execute.call_args_list))
        self.assertTrue(any('autopilot_operational_health_signal' in c.args[0] for c in ocur.execute.call_args_list))

    def test_neon_route_preserves_original_single_connection(self):
        neon,cur=connection([('bridge_school_health_principal',True,True,True,False,False,False,False),('ok',0,0,1),('autopilot_operational_health_signal',)])
        with patch.dict(os.environ,self.env('neon'),clear=True),patch.object(target.psycopg,'connect',return_value=neon) as connect,contextlib.redirect_stdout(io.StringIO()):
            target.main()
        connect.assert_called_once()
        self.assertEqual(connect.call_args.args[0],NEON)
        self.assertTrue(any('autopilot_operational_health_signal' in c.args[0] for c in cur.execute.call_args_list))

    def test_missing_or_unpinned_oracle_dsn_fails_before_any_query(self):
        for value in ['',NEON,ORACLE.replace('127.0.0.1','other.invalid')]:
            with self.subTest(value=value),patch.dict(os.environ,{**self.env(),'AUTOPILOT_HEALTH_DATABASE_URL':value},clear=True),patch.object(target.psycopg,'connect') as connect,contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                target.main()
            connect.assert_not_called()

    def test_oracle_view_is_mandatory_and_critical_is_not_hidden(self):
        cur=MagicMock();cur.fetchone.return_value=(None,)
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            target.check_autopilot_health(cur,required=True)
        cur.fetchone.return_value=('view',)
        cur.fetchall.return_value=[('broken','critical')]
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            target.check_autopilot_health(cur,required=True)


if __name__=='__main__':
    unittest.main()
