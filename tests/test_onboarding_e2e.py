from scripts.test_onboarding_billing import run_checks


def test_onboarding_terms_and_billing_e2e():
    exit_code = run_checks()
    assert exit_code == 0
