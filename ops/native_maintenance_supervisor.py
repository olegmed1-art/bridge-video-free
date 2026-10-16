"""Host lifetime identity and global orphan observation; no permission executor."""
from pathlib import Path
import re
import json
from ops import native_maintenance_lifetime as lifetime
from ops.native_maintenance_workflow_pause import require, digest

class SelfSupervisor:
    seconds = 100

    def __init__(self, source, run):
        group = Path('/proc/self/cgroup').read_text().strip()
        require(group.startswith('0::/system.slice/'), 'RUNTIME_SELF_CGROUP')
        self.unit = group.removeprefix('0::/system.slice/')
        require(self.unit.startswith('bridge-native-ro-' + source[:12] + '-'
                                    + str(run.run_id) + '-' + str(run.attempt) + '-'), 'RUNTIME_SELF_RUN')
        lifetime.assert_self(self.unit, self.seconds)
        state, _, inode = lifetime.identity(self.unit, self.seconds)
        self.record = dict(unit=self.unit, invocation=state['InvocationID'], cgroup_inode=inode)
        self.failed = False

    def assert_alive(self):
        require(not self.failed, 'RUNTIME_SUPERVISOR_FAILED')
        try:
            lifetime.assert_self(self.unit, self.seconds)
            state, _, inode = lifetime.identity(self.unit, self.seconds)
            require(self.record == dict(unit=self.unit, invocation=state['InvocationID'], cgroup_inode=inode),
                    'RUNTIME_SUPERVISOR_CHANGED')
        except BaseException:
            self.failed = True
            raise

    def assert_exclusive(self):
        """Reject orphaned supervisors from ANY scope; never stop another unit."""
        self.assert_alive()
        result = lifetime.ctl('list-units', 'bridge-native-ro-*', '--all', '--plain', '--no-legend', '--no-pager')
        require(result.returncode == 0 and len(result.stdout) <= 65536, 'RUNTIME_UNIT_INVENTORY')
        names = []
        for line in result.stdout.decode('ascii').splitlines():
            fields = line.split()
            require(len(fields) >= 4 and re.fullmatch(lifetime.UNIT, fields[0]), 'RUNTIME_UNIT_INVENTORY_ROW')
            names.append(fields[0])
        require(self.unit in names and len(names) <= 128 and len(names) == len(set(names)),
                'RUNTIME_UNIT_INVENTORY_INCOMPLETE')
        groups = list(Path('/sys/fs/cgroup/system.slice').iterdir())
        require(len(groups) <= 4096, 'RUNTIME_CGROUP_INVENTORY_SIZE')
        matching = [group for group in groups if group.name.startswith('bridge-native-ro-')]
        require(len(matching) <= 128 and self.unit in {group.name for group in matching}
                and all(re.fullmatch(lifetime.UNIT, group.name) and group.name in names
                        for group in matching), 'RUNTIME_UNLISTED_CGROUP')
        for name in names:
            if name == self.unit:
                continue
            state = lifetime.show(name)
            require(state.get('ActiveState') in ('inactive', 'failed') and state.get('MainPID') == '0',
                    'RUNTIME_OTHER_SUPERVISOR_ACTIVE')
            group = Path('/sys/fs/cgroup/system.slice', name)
            if group.exists():
                require(group.is_dir() and 'populated 0' in (group/'cgroup.events').read_text(),
                        'RUNTIME_OTHER_CGROUP_ACTIVE')
        self.assert_alive()



class StageSupervisor(SelfSupervisor):
    seconds = lifetime.STAGE_RUNTIME_SECONDS


class PriorSupervisors:
    """Inspect previously recorded unique units/cgroups without stopping them."""
    def __init__(self, records, accepted_digest):
        require(type(records) is list and len(records) <= 16
                and digest(records) == accepted_digest, 'DRAIN_PRIOR_HOST_NOT_ACCEPTED')
        self.records = json.loads(json.dumps(records))
        self.accepted = accepted_digest
        names = set()
        for record in self.records:
            require(type(record) is dict and set(record) == {'unit', 'invocation', 'cgroup_inode'}
                    and re.fullmatch(lifetime.UNIT, record['unit'])
                    and re.fullmatch('[0-9a-f]{32}', record['invocation'])
                    and type(record['cgroup_inode']) is int and record['cgroup_inode'] > 0
                    and record['unit'] not in names, 'DRAIN_PRIOR_HOST_IDENTITY')
            names.add(record['unit'])

    def assert_drained(self):
        require(digest(self.records) == self.accepted, 'DRAIN_PRIOR_HOST_CHANGED')
        current = Path('/proc/self/cgroup').read_text().strip()
        for record in self.records:
            unit = record['unit']
            require(current != '0::/system.slice/' + unit, 'DRAIN_CANNOT_EXCLUDE_SELF')
            result = lifetime.ctl('show', unit, '--property=LoadState,ActiveState,MainPID,InvocationID')
            require(len(result.stdout) < 16384, 'DRAIN_PRIOR_HOST_RESPONSE')
            fields = dict(line.split('=', 1) for line in result.stdout.decode().splitlines() if '=' in line)
            if fields.get('LoadState') == 'not-found':
                require(not Path('/sys/fs/cgroup/system.slice', unit).exists(), 'DRAIN_PRIOR_CGROUP_PRESENT')
            else:
                require(result.returncode == 0 and fields.get('LoadState') == 'loaded'
                        and fields.get('ActiveState') in ('inactive', 'failed') and fields.get('MainPID') == '0'
                        and fields.get('InvocationID') == record['invocation'], 'DRAIN_PRIOR_HOST_ACTIVE')
                require(lifetime.empty(Path('/sys/fs/cgroup/system.slice', unit), record['cgroup_inode']),
                        'DRAIN_PRIOR_CGROUP_ACTIVE')
