from __future__ import annotations

import bbo100_blind_evaluate as target


def main() -> None:
    # Pure parsing helpers must remain deterministic and must not touch DDS.
    assert target.clean_call('pass') == 'P'
    assert target.clean_call('dbl') == 'X'
    assert target.clean_call('3NT') == '3N'

    contract, declarer, dummy, strain = target.final_contract_and_declarer(
        '1', ['1H', 'P', '2H', 'P', 'P', 'P']
    )
    assert contract == '2H'
    assert declarer == 'S'
    assert dummy == 'N'
    assert strain == 'H'

    rows = [
        {
            'family': 'opening_lead',
            'predicted_dd_regret': 0,
            'recorded_dd_regret': 1,
            'predicted_zero_regret': True,
            'exact_match_recorded': False,
            'assistant_vs_recorded': 'better',
        },
        {
            'family': 'opening_lead',
            'predicted_dd_regret': 2,
            'recorded_dd_regret': 2,
            'predicted_zero_regret': False,
            'exact_match_recorded': True,
            'assistant_vs_recorded': 'equal',
        },
    ]
    aggregate = target.aggregate(rows)
    overall = aggregate['overall']
    assert overall['tasks'] == 2
    assert overall['zero_dd_regret'] == 1
    assert overall['mean_dd_regret'] == 1.0
    assert overall['assistant_better_than_recorded'] == 1
    assert overall['assistant_equal_to_recorded'] == 1
    assert overall['assistant_worse_than_recorded'] == 0

    # The immutable benchmark constants are part of the evidence contract.
    assert target.EXPECTED_TASK_PACKET_SHA256 == '4ee99c708bb997f28101c03367ef74eaf8c7242a413d1869685723d7b6b46c0f'
    assert target.PREDICTION_GATE_COMMIT == '5a6c0f015effc7a9a916591f52da100110fa7beb'
    assert target.PREDICTION_COMMIT == '2426531ab1cc02bf94ee6a4178a56a6874abb1aa'

    print('BBO100_BLIND_EVALUATOR_SELFTEST: PASS')


if __name__ == '__main__':
    main()
