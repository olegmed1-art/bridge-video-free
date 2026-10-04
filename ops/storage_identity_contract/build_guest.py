from pathlib import Path
import ast,hashlib
base=Path(__file__).resolve().parent
raw=(base.parent/'storage_followup/diagnostic_guest.py').read_bytes()
assert hashlib.sha256(raw).hexdigest()=='5912a5a6631b4c655b382b1104c56ee1f3c677839f80d0208bc5b02445193393'
s=raw.decode();lines=s.splitlines(True)
remove=[n for n in ast.parse(s).body if isinstance(n,ast.FunctionDef) and n.name in ('graph_recheck','uv_startup','main')]
for n in sorted(remove,key=lambda n:n.lineno,reverse=True):del lines[n.lineno-1:n.end_lineno]
s=''.join(lines);s=s[:s.index("if __name__=='__main__':main()")]
s=s.replace("safe[k]=safe_path(v) if v else ''","safe[k]=safe_unit_path(v) if k=='FragmentPath' and v else safe_path(v) if v else ''")
s=s.replace('safe_path(current)','safe_unit_path(current)').replace('safe_path(posixpath.join(current,*pending))','safe_unit_path(posixpath.join(current,*pending))')
s+=(base/'identity_helpers.py').read_text()+'\n'+(base/'identity_main.py').read_text()+"\nif __name__=='__main__':main()\n"
ast.parse(s);(base/'diagnostic_guest.py').write_text(s,encoding='utf-8',newline='\n')
print('GUEST_SHA',hashlib.sha256(s.encode()).hexdigest())
