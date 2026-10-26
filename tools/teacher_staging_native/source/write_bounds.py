"""Fail-closed statement/row limits around the pinned, caller-owned SQL connection."""
from collections import Counter
import re

CORE_TABLES=frozenset('analysis_run analysis_run_input analysis_run_output artifact artifact_version asset asset_location changeset decision domain_event episode evidence evidence_link ingestion_item ingestion_run learning_interaction media_asset outbox_message source source_asset transcript transcript_segment'.split())

def limits_for(master, candidate_count):
    transcripts=master.get('transcript') or []
    valid={str(s['segment_id']) for s in transcripts if s.get('segment_id')}
    episodes=master.get('episodes') or []; cycles=master.get('learning_interactions') or []; decisions=master.get('decisions') or []
    def refs(row): return set(str(v) for v in (row.get('evidence') or []) if v)&valid
    referenced=set().union(*(refs(row) for row in episodes+cycles+decisions)) if episodes+cycles+decisions else set()
    caps={name:1 for name in CORE_TABLES}
    caps.update(asset=2,asset_location=2,changeset=2,analysis_run_output=2,
                episode=len(episodes)+len(cycles),decision=len(decisions),
                transcript_segment=len(transcripts),evidence=sum(str(s.get('segment_id') or '') in referenced for s in transcripts),
                evidence_link=sum(len(refs(row)) for row in episodes+cycles+decisions),
                source_identity=1,analysis_candidate=candidate_count+1)
    return {'table_attempted_row_caps':caps,'max_attempted_write_rows':sum(caps.values()),
            'max_sql_calls':sum(caps.values())*3+100,'max_statement_chars':16000,
            'core_write_tables':sorted(CORE_TABLES),'composition_write_tables':sorted(caps)}

class WriteBudget:
    def __init__(self,limits):
        self.limits=limits; self.rows=Counter(); self.calls=0; self.phase='core'; self.phase_tables={}
    def inspect(self,query,nrows=1):
        if not isinstance(query,str) or len(query)>self.limits['max_statement_chars'] or ';' in query:
            raise ValueError('SQL_STATEMENT_SHAPE_OUTSIDE_PINNED_WRITER')
        q=' '.join(query.split()); self.calls+=1
        if self.calls>self.limits['max_sql_calls']: raise ValueError('SQL_CALL_BUDGET_EXCEEDED')
        match=re.match(r'(INSERT INTO|UPDATE) public\.([a-z_]+)\b',q)
        if match:
            verb,table=match.groups()
            if table not in self.limits['table_attempted_row_caps'] or (verb=='UPDATE' and table!='changeset'):
                raise ValueError('WRITE_TABLE_OUTSIDE_ALLOWLIST')
            if self.rows[table]+nrows>self.limits['table_attempted_row_caps'][table]:
                raise ValueError('TABLE_ROW_BUDGET_EXCEEDED:'+table)
            self.rows[table]+=nrows
            self.phase_tables.setdefault(self.phase,set()).add(table)
            return True
        if not q.startswith(('SELECT ','SHOW ')): raise ValueError('NON_READ_OR_PINNED_WRITE_STATEMENT')
        return False
    def report(self):
        return {'attempted_write_rows':dict(self.rows),'sql_calls':self.calls,
                'phase_write_tables':{k:sorted(v) for k,v in self.phase_tables.items()}}

class BoundedCursor:
    def __init__(self,raw,budget): self.raw=raw; self.budget=budget
    def __enter__(self): self.raw.__enter__(); return self
    def __exit__(self,*args): return self.raw.__exit__(*args)
    def execute(self,query,params=None):
        write=self.budget.inspect(query)
        result=self.raw.execute(query,params)
        if write and self.raw.rowcount>1: raise ValueError('WRITE_AFFECTED_TOO_MANY_ROWS')
        return result
    def executemany(self,query,rows):
        rows=list(rows); write=self.budget.inspect(query,len(rows))
        result=self.raw.executemany(query,rows)
        if write and self.raw.rowcount>len(rows): raise ValueError('WRITE_AFFECTED_TOO_MANY_ROWS')
        return result
    def __getattr__(self,name): return getattr(self.raw,name)

class BoundedConnection:
    def __init__(self,raw,budget): self.raw=raw; self.budget=budget
    def transaction(self,*args,**kwargs): return self.raw.transaction(*args,**kwargs)
    def cursor(self,*args,**kwargs): return BoundedCursor(self.raw.cursor(*args,**kwargs),self.budget)
