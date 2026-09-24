"""Optional protected CLI; the runtime implementation ships with the API."""
from bridge_school_api.incident_db_probe import main

if __name__ == "__main__":
    raise SystemExit(main())
