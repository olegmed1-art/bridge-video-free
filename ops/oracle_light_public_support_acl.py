"""Repair only missing source-matched public support ACLs in two fenced rehearsals."""
import json
import sys
import oracle_light_candidate_restore as candidate


def main():
    assert candidate.run('hostname')=='autopilot-lite-vnic'
    for container,database in ((candidate.SOURCE,candidate.SOURCE_DB),(candidate.TARGET,candidate.DATABASE)):
        item=json.loads(candidate.run('docker','inspect',container))[0]
        assert item['Image']==candidate.IMAGE
        assert item['HostConfig']['NetworkMode']==('none' if container==candidate.SOURCE else 'host')
        if container==candidate.TARGET:
            assert item['Config']['Labels']['managed_by']=='bridge-autopilot-pg-stage-v1'
            assert any(m['Source']=='/srv/autopilot-data/postgresql' and m['Destination']=='/var/lib/postgresql' for m in item['Mounts'])
        identity=json.loads(candidate.sql(container,'postgres',f"SELECT jsonb_build_array(pg_get_userbyid(datdba),datlocprovider,datlocale,EXISTS(SELECT FROM aclexplode(coalesce(datacl,acldefault('d',datdba))) a WHERE a.grantee=0 AND a.privilege_type='CONNECT')) FROM pg_database WHERE datname='{database}';"))
        assert identity==['neondb_owner','b','C.UTF-8',False]
        candidate.sql(container,database,"DO $$ BEGIN IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='bridge_school_app') THEN CREATE ROLE bridge_school_app NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS; END IF; END $$;")
        names=','.join("'"+r+"'" for r in candidate.ROLES)
        assert candidate.sql(container,database,f'SELECT count(*) FROM pg_roles WHERE rolname IN ({names}) AND (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls);')=='0'
        candidate.sql(container,database,'BEGIN;'+candidate.SUPPORT_SQL+'COMMIT;')
        proof=candidate.sql(container,database,'BEGIN; SET SESSION AUTHORIZATION bridge_school_worker_principal; SELECT count(*) FROM public.autopilot_operational_health_signal; ROLLBACK;')
        assert proof=='3'
    candidate.main()
    print(json.dumps({'public_support_acl':'VERIFIED','scope':'two_fenced_rehearsals_only','neon_changed':False}))


if __name__=='__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'public_support_acl':'NOT_CONFIRMED','error_type':type(exc).__name__}))
        sys.exit(2)
