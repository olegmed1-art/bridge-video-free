"""Read-only Light/IAM audit; never print credentials or policy contents."""
import json
import os
import re
import sys

LIGHT = 'ocid1.instance.oc1.eu-frankfurt-1.antheljtruoejaicnbolm4whz6iex5lduf4gvk42jaj5maselesau66vvxpq'
TENANCY = 'ocid1.tenancy.oc1..aaaaaaaa52xuylcexzuwuqj4un36qchvmcxqtggmthadvoiho75r6vbkv24q'


def scalar(value, key):
    lines = [line.strip() for line in value.replace('\r', '').splitlines() if line.strip()]
    matches = [line.split('=', 1)[1].strip() for line in lines if line.startswith(key + '=')]
    if len(matches) == 1 and matches[0]:
        return matches[0]
    if not matches and len(lines) == 1 and '=' not in lines[0]:
        return lines[0]
    raise ValueError('ambiguous configuration')


def exact_light_rule(rule):
    return bool(re.fullmatch(r"\s*instance\.id\s*=\s*'" + re.escape(LIGHT) + r"'\s*", rule or ''))


def clients(config, key):
    import oci
    config = dict(config, key_content=key)
    signer = oci.signer.Signer(config['tenancy'], config['user'], config['fingerprint'],
                               None, private_key_content=key)
    kwargs = dict(signer=signer, timeout=(10, 30), retry_strategy=oci.retry.NoneRetryStrategy())
    return oci.core.ComputeClient(config, **kwargs), oci.identity.IdentityClient(config, **kwargs)


def main():
    import oci
    config = {key: scalar(os.environ[env], key) for key, env in (
        ('user', 'OCI_USER'), ('tenancy', 'OCI_TENANCY'),
        ('fingerprint', 'OCI_FINGERPRINT'), ('region', 'OCI_REGION'))}
    if config['tenancy'] != TENANCY or config['region'] != 'eu-frankfurt-1':
        raise ValueError('target mismatch')
    key = os.environ['OCI_KEY'].replace('\\r', '').replace('\\n', '\n')
    compute, iam = clients(config, key)
    result = {'mode': 'READ_ONLY', 'write_permission': 'NOT_TESTED', 'oci_resources_changed': False}

    def read(label, fn, *args, **kw):
        try:
            data = fn(*args, **kw).data
            result[label] = 'OK'
            return data
        except oci.exceptions.ServiceError as exc:
            result[label] = {'status': exc.status, 'code': exc.code}
            return None

    instance = read('light_instance_read', compute.get_instance, LIGHT)
    if instance is None:
        print(json.dumps(result, sort_keys=True))
        return 2
    if instance.display_name != 'bridge-school-autopilot-lite' or instance.compartment_id != TENANCY:
        raise ValueError('instance identity mismatch')
    result['light_state'] = instance.lifecycle_state
    result['agent_plugins_disabled'] = instance.agent_config.are_all_plugins_disabled
    result['run_command_desired_state'] = [p.desired_state for p in (instance.agent_config.plugins_config or [])
                                           if p.name == 'Compute Instance Run Command']
    def pages(fn, *args, **kw):
        return oci.pagination.list_call_get_all_results(fn, *args, **kw)
    groups = read('dynamic_groups_read', pages, iam.list_dynamic_groups, TENANCY)
    if groups is not None:
        result['dynamic_group_count'] = len(groups)
        result['exact_light_dynamic_group_count'] = sum(exact_light_rule(g.matching_rule) for g in groups)
        result['rules_mentioning_light_count'] = sum(LIGHT in (g.matching_rule or '') for g in groups)
    memberships = read('api_user_memberships_read', pages, iam.list_user_group_memberships,
                       TENANCY, user_id=config['user'])
    if memberships is not None:
        result['api_user_group_count'] = len(memberships)
    policies = read('root_policies_read', pages, iam.list_policies, TENANCY)
    if policies is not None:
        result['root_policy_count'] = len(policies)
        # Text presence is NOT effective authorization; no policy text is published.
        statements = [s.lower() for p in policies for s in p.statements]
        result['run_command_execution_statement_present'] = any('instance-agent-command-execution-family' in s for s in statements)
        result['iam_management_statement_present_not_authorization'] = any(
            'manage policies' in s or 'manage dynamic-groups' in s or 'manage all-resources' in s
            for s in statements)
    print(json.dumps(result, sort_keys=True))
    return 0 if all(result.get(k) == 'OK' for k in
                    ('dynamic_groups_read', 'api_user_memberships_read', 'root_policies_read')) else 2


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        # Deliberately exclude SDK messages, request headers and credential values.
        print(json.dumps({'audit': 'FAILED', 'error_type': type(exc).__name__}))
        sys.exit(2)
