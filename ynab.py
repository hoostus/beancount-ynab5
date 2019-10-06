import requests
import datetime
import logging
from collections import namedtuple
from decimal import Decimal
import sys
import re
import string

import beancount.loader
import beancount.core

logging.basicConfig(format='%(asctime)-15s %(message)s')

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

def get_ynab_accounts(auth, budget_id):
    result = {}
    response = requests.get(f'{API}/{budget_id}/accounts', headers=auth)
    response.raise_for_status()
    accounts = response.json()['data']['accounts']
    for a in accounts:
        a['name'] = ynab_normalize(a['name'])
        account = make_tuple('Account', a)
        result[account.id] = account
    return result

def get_ynab_categories(auth, budget_id):
    category_result = {}
    group_result = {}
    response = requests.get(f'{API}/{budget_id}/categories', headers=auth)
    response.raise_for_status()
    category_groups = response.json()['data']['category_groups']
    for g in category_groups:
        g['name'] = ynab_normalize(g['name'])
        group = make_tuple('CategoryGroup', g)
        group_result[group.id] = group
        for c in group.categories:
            c['name'] = ynab_normalize(c['name'])
            category = make_tuple('Category', c)
            category_result[category.id] = category

    return group_result, category_result

def ynab_normalize(name):
    table = str.maketrans('', '', string.punctuation)
    no_punctuation = name.translate(table)
    no_spaces = no_punctuation.replace(' ', '-')
    return no_spaces

def fmt_ynab_category(id, groups, categories):
    c = categories[id]
    group_id = c.category_group_id
    g = groups[group_id]

    n = f'{g.name}:{c.name}'
    return n

def list_ynab_ids(account_mapping, accounts, groups, categories):
    def pretty_print(ids, formatter):
        for item in sorted(ids.items(), key=lambda x: x[1]):
            print(item[0], end=' ')
            print(formatter(item[1]))
            bean_account = account_mapping.get(item[0], '(none)')
            print(' ' * 36, bean_account)

    pretty_print(accounts, formatter=lambda x: x.name)
    pretty_print(categories, formatter=lambda x: fmt_ynab_category(x.id, groups, categories))

def get_target_account(txn):
    if txn.category_id:
        return to_bean(txn.category_id)
    else:
        return to_bean(txn.transfer_account_id)

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(
        description="Import from YNAB5 web app to beancount statements."
    )
    parser.add_argument('bean', help='Path to the beancount file.')
    parser.add_argument('--since', help='Format: YYYY-MM-DD; 2016-12-30. Only process transactions after this date. This will include transactions that occurred exactly on this date.')
    parser.add_argument('--ynab-token', help='Your YNAB API token.', required=True)
    parser.add_argument('--budget', help='Name of YNAB budget to use. Only needed if you have multiple budgets.')
    parser.add_argument('--account-prefix', help='Prefix in beancount of YNAB accounts.', default='Assets')
    parser.add_argument('--budget-category-prefix', help='Prefix in beancount of YNAB budget categories.', default='Expenses')
    parser.add_argument('--list-ynab-ids', action='store_true', default=False, help='Instead of running normally. Simply list the YNAB ids for each budget category.')
    parser.add_argument('--skip-starting-balances', action='store_true', default=False, help='Ignore any starting balance statements in YNAB.')
    args = parser.parse_args()
    if args.since:
        args.since = datetime.datetime.strptime(args.since, "%Y-%m-%d")

    # to actually log in to YNAB we need to add this header to all requests.
    auth_header = {'Authorization': f'Bearer {args.ynab_token}'}

    logging.info('Fetching budget metadata')
    budget = get_budget(auth_header, budget=args.budget)

    logging.info('Loading YNAB account UUIDs from beancount file')
    account_mapping = build_account_mapping(args.bean)

    logging.info('Fetching YNAB account metadata')
    ynab_accounts = get_ynab_accounts(auth_header, budget.id)
    logging.info('Fetching YNAB budget category metadata')
    ynab_category_groups, ynab_categories = get_ynab_categories(auth_header, budget.id)

    if args.list_ynab_ids:
        list_ynab_ids(account_mapping, ynab_accounts, ynab_category_groups, ynab_categories)
        sys.exit(0)

    transactions = get_transactions(auth_header, budget.id, since=args.since)
    
    # TODO: we can reuse this to make future fetches incremental. Where should we stash this?
    server_knowledge = transactions['server_knowledge']

    # We only import transactions once they have been cleared (reconciled) on YNAB. This hopefully removes
    # the need to update things we've already downloaded. That is, we want to treat cleared transactions as immutable
    # but uncleared transactions are still mutable.
    # TODO: Is it necessary to skip deleted transactions here?
    cleared = [t for t in transactions['transactions'] if t['cleared'] == 'cleared' and not t['deleted']]

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
        if id in ynab_accounts:
            bean_default = f'{args.account_prefix}:{ynab_accounts[id].name}'
        elif id in ynab_categories:
            bean_default = f'{args.budget_category_prefix}:{fmt_ynab_category(id, ynab_category_groups, ynab_categories)}'
        else:
            bean_default = id
        return account_mapping.get(id, bean_default)

    r = [x.id for x in ynab_category_groups.values() if x.name == ynab_normalize('Internal Master Category')]
    assert len(r) == 1
    ynab_internal_master_category_id = r[0]
    r = [x.id for x in ynab_categories.values() if x.name == ynab_normalize('Inflows') and x.category_group_id == ynab_internal_master_category_id]
    assert len(r) == 1
    inflows_category_id = r[0]

    # TODO: duplicate prevention
    imported_transactions = set()
    # go through beancount and add all the transactions that already have a ynab-id...

    for t in cleared:
        t = make_transaction(t)

        # TODO: Skip off budget accounts. They don't have enough information to make
        # a double-entry (they only have one leg)
        # if not t.category_id: continue

        if args.skip_starting_balances:
            if t.payee_name == 'Starting Balance' and t.category_id == inflows_category_id:
                continue

        print(f'{t.date} * "{t.payee_name}" {fmt_memo(t.memo)}')
        print(f'  ynab-id: "{t.id}"')
        print(f'  {to_bean(t.account_id):<50}{from_milli(t.amount):>10} {commodity}')
        # Next check if we are looking at a split transaction or a normal one...
        if t.subtransactions:
            for sub in t.subtransactions:
                # we have to reverse the sign on the amount of the subtransaction because YNAB's value
                # is telling us "decrease the budget by this amount" but beancount wants us to say
                # "increase our expenses by this amount"
                print(f'  {get_target_account(sub):<50}{-from_milli(sub.amount):>10} {commodity} ; {sub.memo}')
        else:
            print(f'  {get_target_account(t)}')
        print()
