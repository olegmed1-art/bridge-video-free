from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/oracle-dds3-tls-renewal.yml"


def test_tls_renewal_is_fixed_scope_and_request_bound():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "ops/oracle-dds3-tls-renewal-requests/*.json" in text
    assert "renew-expired-certificate" in text
    assert "expected exactly one TLS renewal request and no other changes" in text
    assert "158.180.47.161" in text
    assert "SHA256:NXmGcng3fzof9b6Hs5Xgh4yYnzxGyVwa/EcfOxu0WPk" in text
    assert "StrictHostKeyChecking=yes" in text
    assert "StrictHostKeyChecking=no" not in text


def test_tls_renewal_preserves_dds3_and_normal_trust_validation():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "http://127.0.0.1:8080/readyz" in text
    assert "systemctl is-active --quiet assistant-lab.service nginx" in text
    assert "systemctl start dds3-cert-renew.service" in text
    assert "ExecStart=/opt/certbot/bin/certbot renew --quiet --cert-name 158.180.47.161" in text
    assert "OnCalendar=*-*-* 00,12:17:00" in text
    assert "systemctl daemon-reload" in text
    assert "--preferred-profile shortlived" in text
    assert '--ip-address "$PUBLIC_IP"' in text
    assert "openssl x509 -checkend 86400" in text
    assert "systemctl enable --now dds3-cert-renew.timer" in text
    assert "ORACLE_DDS3_PUBLIC_TLS_PASS" in text
    assert "curl -k" not in text
    assert "--insecure" not in text


def test_tls_renewal_has_bounded_rollback_and_no_video_path():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert 'rollback --cert-name "$PUBLIC_IP" --non-interactive' in text
    assert "systemctl disable --now dds3-cert-renew.timer" in text
    assert "rm -f /etc/systemd/system/dds3-cert-renew.service" in text
    assert "rm -f /etc/letsencrypt/renewal-hooks/deploy/10-reload-nginx" in text
    assert "TLS_RENEWAL_ROLLBACK_ATTEMPTED" in text
    assert "submit-drive-base64" not in text
    assert "universal-video" not in text.lower()
    assert "SCHOOL CANON" not in text
