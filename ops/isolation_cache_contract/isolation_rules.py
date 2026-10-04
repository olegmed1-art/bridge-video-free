"""Exact observed upstream exceptions; no mutation targets are added."""
from protocol import require

def validate_mounts(rows):
    require(set(rows)=={'-.mount','tmp.mount'},'MOUNT_ROWS')
    for name,row in rows.items():
        require(row.get('Id')==name and row.get('Names','').split()==[name],'MOUNT_IDENTITY')
        require(row.get('StopWhenUnneeded')=='no','MOUNT_GC_RISK')
        for key in ('PartOf','BindsTo','PropagatesStopTo','StopPropagatedFrom','Upholds','UpheldBy'):
            require(key in row and not row[key],'MOUNT_PROPAGATION_UNKNOWN')
        for key in ('Conflicts','ConflictedBy'):
            require(key in row and set(row[key].split())<={'umount.target'},'MOUNT_CONFLICT_UNKNOWN')
    root=rows['-.mount'];tmp=rows['tmp.mount']
    require(root.get('LoadState')=='loaded' and root.get('ActiveState')=='active'
            and root.get('Where')=='/','ROOT_MOUNT_CHANGED')
    require(tmp.get('LoadState')=='not-found' and tmp.get('ActiveState')=='inactive'
            and tmp.get('FragmentPath')=='' and tmp.get('DropInPaths')=='','TMP_MOUNT_CHANGED')

def classify_jobs(jobs, targets, protected):
    """No unknown job is assumed harmless from graph/name disjointness.

    Observed boot jobs can execute arbitrary code or change a backing filesystem.
    Empty result means safe to proceed; classified blockers can be waited on.
    """
    out=[]
    for job in jobs:
        unit=job['unit']
        if unit in targets:reason='TARGET_JOB'
        elif unit in protected or unit in ('-.mount','tmp.mount'):reason='PRESERVED_OR_UPSTREAM_JOB'
        elif unit in ('cloud-init.target','cloud-final.service','systemd-update-utmp-runlevel.service') or unit.endswith(('.mount','.device')) or unit.startswith('systemd-fsck@'):
            reason='BOOT_OR_FILESYSTEM_MUTATOR'
        else:reason='UNPROVEN_JOB'
        out.append({'unit':unit,'type':job['type'],'state':job['state'],'reason':reason})
    return out
