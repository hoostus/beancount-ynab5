import datetime
import logging
from collections import namedtuple
from decimal import Decimal
import sys
import re
import string
import argparse

import beancount.loader
import beancount.core

import time
import asyncio
import tempfile

try:
    import requests
except ImportError:
    requests = None

try:
    import aiohttp
except ImportError:
    aiohttp = None

assert requests or aiohhtp, "Must have either the requests module installed or the aiohttp module installed."

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

def budget_from_json(budget, json_budgets):
    if len(json_budgets) > 1:
        if not budget:
            raise Exception('No budget specified.', [a['name'] for a in json_budgets])
        else:
            b = [a for a in json_budgets if a['name'] == budget]
            if len(b) != 1:
                raise Exception(f'Could not find any budget with name {budget}.')
            b = b[0]
            return make_budget(b)
    else:
        b = json_budgets.values()[0]
        return make_budget(b)

def get_budget(auth, budget=None):
    response = requests.get(API, headers=auth)
    response.raise_for_status()
    d = response.json()
    return budget_from_json(budget, d['data']['budgets'])

# Unlike the other YNAB fetchers, this returns the raw JSON instead of the
# converted namedtuples. Should we change this to do the same? Make this a
# generator?
def get_transactions(auth, budget_id, since=None):
    if since:
        logging.info(f'Only fetching transactions since {since}.')
        response = requests.get(f'{API}/{budget_id}/transactions?since_date={since}', headers=auth)
    else:
        response = requests.get(f'{API}/{budget_id}/transactions', headers=auth)
    response.raise_for_status()
    transactions = response.json()
#    with open('txn.json', 'w+') as f:
#        f.write(response.text)
    return transactions['data']['transactions']

def build_account_mapping(entries):
    mapping = {}

    for entry in entries:
        if isinstance(entry, beancount.core.data.Open):
            if 'ynab-id' in entry.meta:
                mapping[entry.meta['ynab-id']] = entry.account
    return mapping

def accounts_from_json(json_accounts):
    result = {}
    for a in json_accounts:
        a['name'] = ynab_normalize(a['name'])
        account = make_tuple('Account', a)
        result[account.id] = account
    return result

def get_ynab_accounts(auth, budget_id):
    result = {}
    response = requests.get(f'{API}/{budget_id}/accounts', headers=auth)
    response.raise_for_status()
    return accounts_from_json(response.json()['data']['accounts'])

def categories_from_json(json_categories):
    category_result = {}
    group_result = {}

    # categories come as a nested structure with groups at the top
    # and the actual categories underneath the group level
    for g in json_categories:
        g['name'] = ynab_normalize(g['name'])
        group = make_tuple('CategoryGroup', g)
        group_result[group.id] = group
        for c in group.categories:
            c['name'] = ynab_normalize(c['name'])
            category = make_tuple('Category', c)
            category_result[category.id] = category

    return group_result, category_result

def get_ynab_categories(auth, budget_id):
    response = requests.get(f'{API}/{budget_id}/categories', headers=auth)
    response.raise_for_status()
    return categories_from_json(response.json()['data']['category_groups'])

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

def get_target_account(txn, adjustment_account):
    # subtransactions don't have a payee_name attribute, so we do this roundabout
    # check instead....
    if (getattr(txn, 'payee_name', None) == 'Reconciliation Balance Adjustment'
        and txn.memo == 'Entered automatically by YNAB'
        and adjustment_account):
        logging.info(f'Using {adjustment_account} for reconciliation balance adjustment on transaction {txn.id}.')
        return adjustment_account
    elif txn.category_id:
        return to_bean(txn.category_id)
    elif txn.transfer_account_id:
        return to_bean(txn.transfer_account_id)
    else:
        # This can only happen with YNAB's Tracking accounts. We can't generate
        # a valid beancount entry, so we generate an error mesage.
        return '; FIXME. Error could only generate one leg from YNAB data.'

def get_ynab_data(token, budget_name, since):
    logging.info('Using regular fetcher for YNAB.')
    # BENCHMARK: benchmark vanilla vs. async
    start_timing = time.time()

    # to actually log in to YNAB we need to add this header to all requests.
    auth_header = {'Authorization': f'Bearer {token}'}

    logging.info('Fetching YNAB budget metadata.')
    budget = get_budget(auth_header, budget=budget_name)

    logging.info('Fetching YNAB account metadata.')
    ynab_accounts = get_ynab_accounts(auth_header, budget.id)

    logging.info('Fetching YNAB budget category metadata.')
    ynab_category_groups, ynab_categories = get_ynab_categories(auth_header, budget.id)

    logging.info('Fetching YNAB transactions.')
    ynab_transactions = get_transactions(auth_header, budget.id, since=since)

    # BENCHMARK: benchmark vanilla vs. async
    end_timing = time.time()
    logging.info(f'YNAB http requests took: {end_timing - start_timing}.')

    return budget, ynab_accounts, ynab_category_groups, ynab_categories, ynab_transactions

def get_ynab_data_async(token, budget_name, since):
    logging.info('Using asynchronous fetcher for YNAB.')
    start_timing = time.time()

    # to actually log in to YNAB we need to add this header to all requests.
    auth_header = {'Authorization': f'Bearer {token}'}

    async def fetch(url, session):
        async with session.get(url, headers=auth_header) as response:
            return await response.json()

    async def run(r):
        budget_id = None
        tasks = []

        async with aiohttp.ClientSession(raise_for_status=True) as session:
            # We have to load the budget metadata first, to look up the ID
            # for the budget name we are given
            async with session.get(API, headers=auth_header) as response:
                d = await response.json()
                budget = budget_from_json(budget_name, d['data']['budgets'])

                # Then we can do the next 3 in parallel.
                task = asyncio.ensure_future(fetch(f'{API}/{budget.id}/accounts', session))
                tasks.append(task)

                task = asyncio.ensure_future(fetch(f'{API}/{budget.id}/categories', session))
                tasks.append(task)

                if since:
                    logging.info(f'Only fetching transactions since {since}.')
                    task = asyncio.ensure_future(fetch(f'{API}/{budget.id}/transactions?since_date={since}', session))
                else:
                    task = asyncio.ensure_future(fetch(f'{API}/{budget.id}/transactions', session))
                tasks.append(task)

            responses = await asyncio.gather(*tasks)
            accounts = accounts_from_json(responses[0]['data']['accounts'])
            category_groups, categories = categories_from_json(responses[1]['data']['category_groups'])
            transactions = responses[2]['data']['transactions']

            return budget, accounts, category_groups, categories, transactions

    loop = asyncio.get_event_loop()
    future = asyncio.ensure_future(run(4))
    budget, accounts, category_groups, categories, transactions = loop.run_until_complete(future)

    # BENCHMARK: benchmark vanilla vs. async
    end_timing = time.time()
    logging.info(f'YNAB http requests took: {end_timing - start_timing}')

    return budget, accounts, category_groups, categories, transactions

def get_existing_ynab_transaction_ids(entries):
    seen = set()
    for e in entries:
        # We don't want to add Nones to the set
        if isinstance(e, beancount.core.data.Transaction) and 'ynab-id' in e.meta:
            seen.add(e.meta['ynab-id'])
    return seen

class NegateAction(argparse.Action):
    def __call__(self, parser, ns, values, option):
        setattr(ns, self.dest, option[2:9] != 'disable')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="Import from YNAB5 web app to beancount statements.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument('bean', help='Path to the beancount file.', nargs='?', default=None)
    parser.add_argument('--since', help='Format: YYYY-MM-DD; 2016-12-30. Only process transactions after this date. This will include transactions that occurred exactly on this date.')
    parser.add_argument('--ynab-token', help='Your YNAB API token.', required=True)
    parser.add_argument('--budget', help='Name of YNAB budget to use. Only needed if you have multiple budgets.')
    parser.add_argument('--list-ynab-ids', action='store_true', default=False, help='Instead of running normally. Simply list the YNAB ids for each budget category.')
    parser.add_argument('--skip-starting-balances', action='store_true', default=False, help='Ignore any starting balance statements in YNAB.')
    parser.add_argument('--debug', action='store_true', default=False, help='Print debugging logging to stderr.')
    parser.add_argument('--verbose', action='store_true', default=False, help='Mildly verbose logging to stderr.')
    parser.add_argument('--enable-async-fetch', '--disable-async-fetch', dest='async_fetch', action=NegateAction, default=(aiohttp is not None), nargs=0, help='Use aiohttp to fetch YNAB data in parallel.')
    parser.add_argument('--balance-adjustment-account', help='Account to assign all automatically entered reconciliation balance adjustments.')
    args = parser.parse_args()
    if args.since:
       args.since = datetime.datetime.strptime(args.since, "%Y-%m-%d").date()

    if args.async_fetch and not aiohttp:
        logging.error('Cannot specify --async-fetch if aiohttp is not installed.')
        sys.exit(1)

    if not args.bean:
        # Beancount-ynab5 requires a bean file to be passed on the CLI.
        # It passes this file to beancount.loader.load_file and
        # expects a 3-tuple returned, [entries,errors,options].
        # Changing to accommodate no file is tricky
        # The following provides a workaround.

        # beancount.loader.load_file can handle an empty file, so this passes
        # handling of the no-file problem to beancount
        tempfile = tempfile.NamedTemporaryFile()
        args.bean = tempfile.name

    # structuring it this way means we can specify --verbose AND --debug and it will
    # end up picking the most verbose (i.e. debug)
    log_level = logging.WARN
    if args.verbose:
        log_level = logging.INFO
    if args.debug:
        log_level = logging.DEBUG
    logging.basicConfig(format='%(asctime)-15s %(message)s', level=log_level)

    logging.debug(f'Parsing beancount file {args.bean}')
    beancount_entries, beancount_errors, beancount_options = beancount.loader.load_file(args.bean, log_errors=sys.stderr)
    if beancount_errors:
        sys.exit(1)

    asset_prefix = beancount_options['name_assets']
    expense_prefix = beancount_options['name_expenses']
    income_prefix = beancount_options['name_income']

    logging.debug('Loading YNAB IDs for existing transactions in beancount')
    seen_transactions = get_existing_ynab_transaction_ids(beancount_entries)

    logging.debug('Loading YNAB account UUIDs from beancount file')
    account_mapping = build_account_mapping(beancount_entries)

    if args.async_fetch:
        fetcher = get_ynab_data_async
    else:
        fetcher = get_ynab_data

    budget, ynab_accounts, ynab_category_groups, ynab_categories, ynab_transactions = fetcher(args.ynab_token, args.budget, args.since)

    if args.list_ynab_ids:
        list_ynab_ids(account_mapping, ynab_accounts, ynab_category_groups, ynab_categories)
        sys.exit(0)

    # TODO: we can reuse this to make future fetches incremental. Where should we stash this?
    # server_knowledge = ynab_transactions['server_knowledge']

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

    r = [x.id for x in ynab_category_groups.values() if x.name == ynab_normalize('Internal Master Category')]
    assert len(r) == 1
    ynab_internal_master_category_id = r[0]
    r = [x.id for x in ynab_categories.values() if x.name == ynab_normalize('Inflows') and x.category_group_id == ynab_internal_master_category_id]
    assert len(r) == 1
    inflows_category_id = r[0]

    def to_bean(id):
        if id in ynab_accounts:
            bean_default = f'{asset_prefix}:{ynab_accounts[id].name}'
        elif id == inflows_category_id:
            # special case for the inflows category id
            bean_default = f'{income_prefix}:{fmt_ynab_category(id, ynab_category_groups, ynab_categories)}'
        elif id in ynab_categories:
            bean_default = f'{expense_prefix}:{fmt_ynab_category(id, ynab_category_groups, ynab_categories)}'
        else:
            bean_default = id
        return account_mapping.get(id, bean_default)

    count = 0

    # We only import transactions once they have been reconciled on YNAB. This hopefully removes
    # the need to update things we've already downloaded. That is, we want to treat cleared transactions as immutable
    # but uncleared transactions are still mutable.
    # TODO: Is it necessary to skip deleted transactions here?
    for t in (t for t in ynab_transactions if t['cleared'] == 'reconciled' and not t['deleted']):
        t = make_transaction(t)

        if args.skip_starting_balances:
            # This will skip starting balances in budget accounts but not tracking accounts
            if t.payee_name == 'Starting Balance' and t.category_id == inflows_category_id:
                logging.debug(f'Skipping Starting Balance statement in budget account: {t.date} {to_bean(t.account_id)}')
                continue
            # We also want to skip starting balances in tracking accounts. Tracking
            # accounts won't have a category id
            if t.payee_name == 'Starting Balance' and not t.category_id:
                logging.debug(f'Skipping Starting Balance statement in tracking account: {t.date} {to_bean(t.account_id)}')
                continue

        if not t.category_id and not t.transfer_account_id:
            logging.warning(
                f'Saw a transaction without a category or transfer account id.'
                f' This means the resulting beancount output will be corrupted.'
                f' Manually inspect the transaction and fix it.'
                f' {t.date} {to_bean(t.account_id)} "{t.payee_name}" {from_milli(t.amount)}'
            )

        # Deduplication -- don't process transactions we've already seen
        if t.id in seen_transactions:
            logging.debug(f'Skipping duplicate transaction: {t.date} {t.payee_name}')
            continue
        if t.transfer_transaction_id in seen_transactions:
            logging.debug(f'Skipping duplicate transfer transaction: {t.date} {t.payee_name}')
            continue

        count += 1
        print(f'{t.date} * "{t.payee_name}" {fmt_memo(t.memo)}')
        print(f'  ynab-id: "{t.id}"')
        # To avoid duplicate imports for transfers we need to account for
        # both our id and the other leg of the transfer's id
        seen_transactions.add(t.id)
        if t.transfer_transaction_id: seen_transactions.add(t.transfer_transaction_id)
        print(f'  {to_bean(t.account_id):<50}{from_milli(t.amount):>10} {commodity}')
        # Next check if we are looking at a split transaction or a normal one...
        if t.subtransactions:
            for sub in t.subtransactions:
                # we have to reverse the sign on the amount of the subtransaction because YNAB's value
                # is telling us "decrease the budget by this amount" but beancount wants us to say
                # "increase our expenses by this amount"
                print(f'  {get_target_account(sub, args.balance_adjustment_account):<50}{-from_milli(sub.amount):>10} {commodity} ; {sub.memo}')
                # We need to deduplicate any transfers that happen in a subtransaction...
                if sub.transfer_transaction_id: seen_transactions.add(sub.transfer_transaction_id)
        else:
            print(f'  {get_target_account(t, args.balance_adjustment_account)}')

        print()

    logging.info(f'Imported {count} new transactions.')