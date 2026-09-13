# Oracle manual hold

Owner-only control for periods of interactive testing when the Frankfurt Oracle VM must remain available even if briefly idle.

- `/oracle-hold N` keeps the VM requested-on for N whole hours, where N is 1..12.
- `/oracle-hold off` releases the hold early.
- Starting a hold dispatches the existing bounded `oracle-instance-power.yml` start action.
- Hold state is stored durably as issue comments.
- Automatic idle stop must not act while an unexpired hold exists.
- After hold expiry, the controller preserves the standard ten-minute grace and then uses the existing bounded stop path only when Neon and Universal Video external-work checks are empty.
- No OCI credentials or instance identity are added to the hold workflow.
