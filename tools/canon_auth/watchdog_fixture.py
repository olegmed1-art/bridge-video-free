"""Disposable process-death qualification; fixed anonymous loopback DB ONLY."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from time import sleep
from .launch_contract import Launch, Permit
from .fixed_adapter import FixedAdapter
from .watchdog import watch
from .stage_deadline import supervise
from tools.tournament_pilot.rehearsal import local_connect, DB


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--role",choices=("watchdog","controller"),required=True)
    parser.add_argument("--contract",required=True)
    parser.add_argument("--school",required=True)
    parser.add_argument("--ready",required=True)
    parser.add_argument("--receipt",required=True)
    args=parser.parse_args()
    launch=Launch.parse(json.loads(Path(args.contract).read_text()))
    clock=lambda:datetime.now(timezone.utc)
    with local_connect() as conn:
        assert conn.info.host=="127.0.0.1" and conn.info.port==55432
        assert conn.execute("SELECT current_database()").fetchone()==(DB,)
        adapter=FixedAdapter(conn,args.school,launch,lambda:None,clock)
        if args.role=="watchdog":
            with supervise(launch,"watchdog",clock):
                result=watch(launch,adapter.inspect,adapter.recover,clock,
                    ready=lambda receipt:Path(args.ready).write_text(json.dumps(receipt)))
            Path(args.receipt).write_text(json.dumps(result))
        else:
            # REAL remote stages commit; this process then has NO recovery
            # exception handler. The parent kills it while independent watchdog
            # remains alive and later performs owned revoke.
            for stage,phase in (("baseline","preflight"),("initial","baseline")):
                adapter.execute(stage,Permit(launch.fingerprint,stage,phase,"a"*64,"b"*64,clock()))
            Path(args.ready).write_text('{"status":"FIXTURE_ACTIVE"}')
            while True:
                sleep(.2)


if __name__=="__main__":
    main()
