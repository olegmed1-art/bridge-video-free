Historical test fixture from f6fd9d7d^ (before retirement). Never deploy as a GitHub workflow. Retained solely to regression-test the idle-stop proof protocol; tests separately require the live workflow to remain absent.

Additional historical fixtures, copied byte-for-byte from the last parent before intentional removal:

- `oracle-universal-video-job.yml`: source3391473724834711883ee349e9c7137dd7deec39; removed by0aabbabc05c635de481173f07004afd4d4f2e95d.
- `oracle-instance-power-auto.yml`: sourcef6fd9d7d88b18cc504f0cbe397793794d5b787af; removed by18adca1d5127ec815c95d5fca525136dd0318660.

These files are inert test data outside `.github/workflows`. Legacy tests read them to retain historical transport/receipt/idle protocol coverage; separate assertions require the retired live controllers to remain absent. They are not the current Oracle Light deployment configuration.
