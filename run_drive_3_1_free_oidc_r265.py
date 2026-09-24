#!/usr/bin/env python3
"""Google Drive OAuth entrypoint for bounded Bridge Video r26.5 validation."""
from run_drive_3_1_free_oidc import user_oauth_token
from run_drive_3_1_free_r265 import main as generic_main


if __name__ == "__main__":
    generic_main(user_oauth_token)
