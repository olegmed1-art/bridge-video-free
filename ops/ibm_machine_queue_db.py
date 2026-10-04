"""Fixed read-only Assistant Lab queue observer in the existing owner context."""
import datetime,os,sys
from ops.ibm_machine_queue_protocol import SQL,SQL_SHA,need,parse,canonical,HardCap
def query(conn):
 from database.native_cli_permission_engine import identity
 from ops.native_permission_hold_guard import EXPECTED_TARGET
 from database.native_cli_permission_engine import Target,NeonBinding
 target=Target(**{**EXPECTED_TARGET,"neon":NeonBinding(**EXPECTED_TARGET["neon"])})
 need(conn.read_only is True,"READONLY_CONFIGURATION")
 with conn.transaction():
  conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ ONLY")
  conn.execute("SET LOCAL statement_timeout='2s'")
  conn.execute("SET LOCAL lock_timeout='1s'")
  conn.execute("SET LOCAL search_path='pg_catalog'")
  need(conn.execute("SELECT current_setting('transaction_read_only')").fetchone()==("on",),"READONLY_TRANSACTION")
  identity(conn,target)
  # ACCESS SHARE permits writers, but pins both named relations through counts.
  conn.execute("LOCK TABLE ONLY assistant_lab.control_command, ONLY assistant_lab.job IN ACCESS SHARE MODE")
  locked=conn.execute("SELECT 'assistant_lab.control_command'::regclass::oid,'assistant_lab.job'::regclass::oid").fetchone()
  need(type(locked) is tuple and len(locked)==2 and all(type(x) is int and x>0 for x in locked) and len(set(locked))==2,"LOCKED_RELATION_OIDS")
  # Validate these exact locked OIDs; no views, partitions, inheritance or RLS.
  catalog=conn.execute("""SELECT c.oid,c.relname,c.relkind,c.relrowsecurity,c.relforcerowsecurity,a.amname,
    EXISTS(SELECT 1 FROM pg_catalog.pg_inherits i WHERE i.inhrelid=c.oid OR i.inhparent=c.oid)
    FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
    LEFT JOIN pg_catalog.pg_am a ON a.oid=c.relam
    WHERE c.oid IN ('assistant_lab.control_command'::regclass,'assistant_lab.job'::regclass) AND n.nspname='assistant_lab' ORDER BY c.relname""").fetchall()
  need(catalog==[(locked[0],"control_command","r",False,False,"heap",False),(locked[1],"job","r",False,False,"heap",False)],"QUEUE_CATALOG")
  cur=conn.execute(SQL);values=cur.fetchone()
  row=dict(zip([c.name for c in cur.description],values))
  need(row["database_name"]==target.database and all(type(row[k]) is int and row[k]==0 for k in ("lab_nonterminal","control_nonterminal","null_status_count")) and row["job_rls_off"] is True and row["control_rls_off"] is True,"QUEUE_NOT_ZERO_OR_VISIBLE")
  observed=row["observed_at"];need(isinstance(observed,datetime.datetime) and observed.tzinfo is not None,"SERVER_TIMESTAMP")
  row["observed_at"]=observed.isoformat()
  return row
def entry():
 cap=HardCap(180)
 try:
  # Existing helper and credential reference; no DSN rewrite helper is introduced.
  import psycopg
  from ops.native_maintenance_owner_attest import parameters
  from ops.native_permission_hold_guard import EXPECTED_TARGET
  raw=os.environ.pop("NATIVE_OWNER_DATABASE_URL","")
  with psycopg.connect(**parameters(raw),autocommit=True) as conn:
   del raw;conn.read_only=True
   query(conn)
   target=EXPECTED_TARGET
   print(canonical({"kind":"DB_READY","project":target["neon"]["project_id"],"branch":target["neon"]["branch_id"],"database":target["database"],"role":target["session_owner"]}).decode(),end="",flush=True)
   for raw in iter(lambda:sys.stdin.buffer.readline(65537),b""):
    need(len(raw)<=65536 and raw.endswith(b"\n"),"FRAME_LIMIT");r=parse(raw)
    need(set(r)=={"kind","phase","nonce","host","boot","run","requested_mono","project","branch","database","sql_sha256","sql"} and r["kind"]=="QUEUE_REQUEST" and r["phase"] in ("PRE_STOP","POST_STOP") and (r["project"],r["branch"],r["database"],r["sql_sha256"],r["sql"])==(target["neon"]["project_id"],target["neon"]["branch_id"],target["database"],SQL_SHA,SQL),"FIXED_QUERY_SCOPE")
    row=query(conn);row.update(kind="FIXED_QUERY_RESULT",nonce=r["nonce"]);print(canonical(row).decode(),end="",flush=True)
 except BaseException:
  print(canonical({"kind":"ADAPTER_ERROR"}).decode(),end="",flush=True);return 2
 finally:cap.close()
 return 0
if __name__=="__main__":sys.exit(entry())
