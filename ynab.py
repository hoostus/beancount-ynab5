import requests
import datetime
from collections import namedtuple
from decimal import Decimal
import sys

import beancount.loader
import beancount.core

API = 'https://api.youneedabudget.com/v1/budgets'

def make_budget(n):
    c = n['currency_format']
    n['currency_format'] = make_tuple('CurrencyFormat', c)
    return make_tuple('Budget', n)

def make_transaction(n):
    s = n['subtransactions']
    sub = [make_tuple('Subtransaction', x) for x in s]
    n['subtransactions'] = sub
    return make_tuple('Transaction', n)


def make_tuple(name, d):
    return namedtuple(name, d.keys())(*d.values())

# TODO: how does the default budget work?
# (“last-used” can be used to specify the last used budget and “default” can be used if default budget selection is enabled 
def get_budget(auth, budget=None):
    response = requests.get(API, headers=auth)
    response.raise_for_status()
    d = response.json()
    budgets = d['data']['budgets']
    if len(budgets) > 1:
        if not budget:
            raise Exception('No budget specified.', [a['name'] for a in budgets])
        else:
            b = [a for a in budgets if a['name'] == budget]
            if len(b) != 1:
                raise Exception(f'Could not find any budget with name {budget}.')
            b = b[0]
            return make_budget(b)
    else:
        b = budgets.values()[0]
        return make_budget(b)

def get_transactions(auth, budget_id, since=None):
        # TODO: possible query parameters: since_date, type, last_knowledge_of_server
        response = requests.get(f'{API}/{budget_id}/transactions', headers=auth)
        response.raise_for_status()
        transactions = response.json()
        return transactions['data']

def build_account_mapping(bean_filename):
        entries, errors, options = beancount.loader.load_file(bean_filename)

        mapping = {}

        for entry in entries:
            if isinstance(entry, beancount.core.data.Open):
                if 'ynab-id' in entry.meta:
                    mapping[entry.meta['ynab-id']] = entry.account
        return mapping

def list_ynab_ids(auth, budget_id, account_mapping):
    def pretty_print(ids):
        for item in sorted(ids.items(), key=lambda x: x[1]):
            print(item[0], item[1])
            bean_account = account_mapping.get(item[0], '(none)')
            print(' ' * 36, bean_account)

    ids = {}

    response = requests.get(f'{API}/{budget_id}/accounts', headers=auth)
    response.raise_for_status()
    accounts = response.json()['data']['accounts']
    for a in accounts:
        account = make_tuple('Account', a)
        ids[account.id] = account.name
    pretty_print(ids)

    print()

    ids = {}
    response = requests.get(f'{API}/{budget_id}/categories', headers=auth)
    response.raise_for_status()
    category_groups = response.json()['data']['category_groups']
    for g in category_groups:
        group = make_tuple('CategoryGroup', g)
        for c in group.categories:
            category = make_tuple('Category', c)
            ids[category.id] = f'{group.name}:{category.name}'
    pretty_print(ids)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(
        description="Import from YNAB5 web app to beancount statements."
    )
    parser.add_argument('bean', help='Path to the beancount file.')
    parser.add_argument('--since', help='Format: YYYY-MM-DD; 2016-12-30. Only process transactions after this date. This will include transactions that occurred exactly on this date.')
    parser.add_argument('--ynab-token', help='Your YNAB API token.', required=True)
    parser.add_argument('--budget', help='Name of YNAB budget to use. Only needed if you have multiple budgets.')
    parser.add_argument('--list-ynab-ids', action='store_true', default=False, help='Instead of running normally. Simply list the YNAB ids for each budget category.')
    parser.add_argument('--skip-opening-balances', action='store_true', default=False, help='Ignore any opening balance statements in YNAB.')
    args = parser.parse_args()
    if args.since:
        args.since = datetime.datetime.strptime(args.since, "%Y-%m-%d")

    # to actually log in to YNAB we need to add this header to all requests.
    auth_header = {'Authorization': f'Bearer {args.ynab_token}'}

    budget = get_budget(auth_header, budget=args.budget)

    account_mapping = build_account_mapping(args.bean)

    if args.list_ynab_ids:
        list_ynab_ids(auth_header, budget.id, account_mapping)
        sys.exit(0)

    transactions = get_transactions(auth_header, budget.id, since=args.since)
    
    # TODO: we can reuse this to make future fetches incremental. Where should we stash this?
    server_knowledge = transactions['server_knowledge']

    # We only import transactions once they have been cleared (reconciled) on YNAB. This hopefully removes
    # the need to update things we've already downloaded. That is, we want to treat cleared transactions as immutable
    # but uncleared transactions are still mutable.
    cleared = [t for t in transactions['transactions'] if t['cleared'] == 'cleared']

    # TODO: how do we get this from YNAB and compare against beancount?
    commodity = budget.currency_format.iso_code

    # all amounts are "milliunits" and need to be converted
    def from_milli(n):
        return Decimal(n)/1000

    def fmt_memo(memo):
        if memo:
            return f'"{memo}"'
        else:
            return ''

    def to_bean(id):
        return account_mapping.get(id, id)

    for t in cleared:
        t = make_transaction(t)

        # Skip off budget accounts. They don't have enough information to make
        # a double-entry (they only have one leg)
        if not t.category_id: continue

        # de-dup
        # transfers
        # skip opening balances?

        print(f'{t.date} * "{t.payee_name}" {fmt_memo(t.memo)}')
        print(f'  ynab-id: "{t.id}"')
        print(f'  {to_bean(t.account_id)}{from_milli(t.amount):>30} {commodity}')
        # Next check if we are looking at a split transaction or a normal one...
        if t.subtransactions:
            for sub in t.subtransactions:
                # we have to reverse the sign on the amount of the subtransaction because YNAB's value
                # is telling us "decrease the budget by this amount" but beancount wants us to say
                # "increase our expenses by this amount"
                print(f'  {to_bean(sub.category_id)}{-from_milli(sub.amount):>30} {commodity} ; {sub.memo}')
        else:
            print(f'  {to_bean(t.category_id)}')
        print()
