"""Configure only the requested spending warning; does not link project billing."""
import argparse
import json
import re
from urllib.parse import urlencode

from cloud import Cloud

PROJECT = 'magne-ai-coach-20260915'
PROJECT_NUMBER = '600465847441'

def configure_budget(cloud, billing_account):
    if not isinstance(billing_account, str) or not re.fullmatch(r"[A-F0-9]{6}-[A-F0-9]{6}-[A-F0-9]{6}", billing_account):
        raise ValueError("Supply a valid billing account ID")
    base = f'https://billingbudgets.googleapis.com/v1/billingAccounts/{billing_account}/budgets'
    budgets = []
    page_token = None
    while True:
        query = {'pageSize': 1000}
        if page_token:
            query['pageToken'] = page_token
        page = cloud.rest('GET', base + '?' + urlencode(query))
        budgets.extend(page.get('budgets', []))
        page_token = page.get('nextPageToken')
        if not page_token:
            break
    name = 'AI Coach — NOK 350 monthly warning'
    matching = [b for b in budgets if b.get('displayName') == name and
                b.get('budgetFilter', {}).get('projects') == [f'projects/{PROJECT_NUMBER}']]
    if len(matching) > 1:
        raise SystemExit('More than one matching warning exists; refusing duplicate updates.')
    body = {
        'displayName': name,
        'budgetFilter': {'projects': [f'projects/{PROJECT_NUMBER}'], 'calendarPeriod': 'MONTH',
                         'creditTypesTreatment': 'INCLUDE_ALL_CREDITS'},
        'amount': {'specifiedAmount': {'currencyCode': 'NOK', 'units': '350'}},
        'thresholdRules': [{'thresholdPercent': 1.0, 'spendBasis': 'CURRENT_SPEND'}],
        'notificationsRule': {'disableDefaultIamRecipients': True, 'enableProjectLevelRecipients': True},
    }
    if matching:
        body['name'] = matching[0]['name']
        body['etag'] = matching[0]['etag']
        result = cloud.rest('PATCH', 'https://billingbudgets.googleapis.com/v1/' + body['name'] +
                            '?updateMask=displayName,budgetFilter,amount,thresholdRules,notificationsRule', body)
    else:
        result = cloud.rest('POST', base, body)
    verified = cloud.rest('GET', 'https://billingbudgets.googleapis.com/v1/' + result['name'])
    assert verified['amount']['specifiedAmount']['units'] == '350'
    assert verified['amount']['specifiedAmount']['currencyCode'] == 'NOK'
    assert verified['budgetFilter']['projects'] == [f'projects/{PROJECT_NUMBER}']
    assert verified['notificationsRule']['enableProjectLevelRecipients'] is True
    return verified


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--billing-account", required=True, help="Google Cloud billing account ID; no default is stored")
    args = parser.parse_args()
    try:
        result = configure_budget(Cloud(PROJECT), args.billing_account)
    except ValueError:
        raise SystemExit("Supply a valid billing account ID") from None
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
