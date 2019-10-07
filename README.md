# beancount-ynab5
 Import YNAB5 transactions from the cloud into beancount,
 a plaintext accounting system.

# What it does.

*beancount-ynab5* will import **cleared** transactions from your cloud-based
YNAB5 budget into a beancount file. This allows you to use YNAB5 for its
budgeting, for automatically pulling transactions from various US financial
institutions, and for its mobile app. But you can also sync it to beancount,
which has support for multiple currencies, investments, and other things.

Why only cleared transactions? This importer doesn't handle updating a transaction
that you've already imported. By waiting until the transaction is cleared we (hopefully)
avoid situations where we import something that then later gets updated.

# Requirements.

* requests: https://pypi.org/project/requests/

# Running it.

When you run the script it will output beancount transactions to stdout. You
probably want to tee it or redirect it to a file.

    > python ynab.py --ynab-token FAKE4642488831a79ea13cb291570eae186d9
    2019-10-04 * "Starting Balance" 
        ynab-id: "b79660e1-6554-4dae-97be-9bf7b04764f3"
        Assets:Checking                                    100000000 VND
        Expenses:Internal-Master-Category:Inflows

    2019-10-04 * "Starting Balance" 
        ynab-id: "b8bdf6d5-85fa-4e50-b0a9-d6579b9cb9ae"
        Assets:Wallet                                              0 VND
        Expenses:Internal-Master-Category:Inflows

    2019-10-06 * "Blammo" 
        ynab-id: "08c9f5c0-c764-4c89-bc82-9437556ce316"
        Assets:Checking                                      -100000 VND
        Assets:Wallet                                          75000 VND ; to wallet
        Expenses:Immediate-Obligations:Transportation          25000 VND ; 

In order to get access to your YNAB budget you need to have a Personal Access
Token and provide it to the importer on the command line with the **--ynab-token**
option.

## Get a Personal Access Token for your YNAB account.

Instructions on how to do this are on [YNAB's API website.](https://api.youneedabudget.com/)

The short version: Go to "My Account" then click on "Developer Settings".

Write down the very long token somewhere. You'll need it later.

## YNAB Rate Limits

[YNAB enforces rate limits on its API.](https://api.youneedabudget.com/#rate-limiting)

> An access token may be used for up to 200 requests per hour.
> The limit is reset every clock hour. So, if an access token is used at 12:30 PM
> and for 199 more requests up to 12:45 PM and then hits the limit, any additional
> requests will be forbidden until 1:00 PM. At 1:00 PM you would have the full
> 200 requests allowed again, until 2:00 PM.

It shouldn't be an issue in normal usage -- each run of the importer results in
4 HTTP accesses, so you can run it 50 times an hour -- but just FYI.

# Mapping accounts between YNAB and beancount.

The importer needs to map accounts & budget categories between YNAB and beancount.
You have two options.

1. Rely on the default mapping algorithm.
2. Add metadata to the beancount file explicitly mapping accounts.

## The default algorithm.

1. Any punctuation in the YNAB account name is removed. So *Rent/Mortgage* would
    become *RentMortgage*
1. Any spaces get turned into a hyphen. So *Car Insurance* would become
    *Car-Insurance*
1. All YNAB accounts are given the *Assets* prefix.
1. All YNAB budget categories are given the *Expenses* prefix.
1. Budget groups are used as an additional level of hierarchy.

In the default budget category list that means *Rent & Mortgage* in the group
*Immediate Obligations* becomes *Expenses:Immediate-Obligations:RentMortgage*.

Here are a few other examples of the transformation:

1. Expenses:Immediate-Obligations:RentMortgage (*Rent & Mortgage*)
1. Expenses:True-Expenses:RentersHome-Insurance (*Renter's/Home Insurance*)
1. Expenses:Immediate-Obligations:Interest--Fees (*Interest & Fees*)

## Add a ynab-id to your beancount accounts.

It is also possible to perform an explicit mapping. You might do this if you
don't like the default algorithm or if you want beancount and YNAB to have
different structures & naming.

All you need to do is add a **ynab-id** metadata to any account with the value
of the UUID of the YNAB account you want it to map to.

    2016-01-01 open Expenses:Monthly:Phone
        ynab-id: "9a2fb967-974a-4040-a584-0234d1de7abb"

How do you get that UUID? By using the importer's **--list-ynab-ids** mode.

    > python ynab.py --token $TOKEN my.beanfile --list-ynab-ids
    2e092108-4065-44a3-875e-db77fc2bc48f Just for Fun:Dining Out
                                         Expenses:Everyday:Restaurants
    [...repeats...]

This will list the UUID, the YNAB account, and the associated beancount account
(if any). If there is no beancount account associated with that UUID yet then
*(none)* will be displayed instead, as in this example:

    > python ynab.py --token $TOKEN my.beanfile --list-ynab-ids
    f7aa4b9e-7fa4-4294-a5ec-ded5a54f5ff2 Just for Fun:Fun Money
                                         (none)

# Skipping 'Starting Balance' statements in YNAB.

YNAB will generate Starting Balance statements that are, essentially, income
from nowhere.

    2019-10-04 * "Starting Balance" 
        ynab-id: "b79660e1-6554-4dae-97be-9bf7b04764f3"
        Assets:Checking                                    5,000 USD
        Expenses:Internal-Master-Category:Inflows

In many cases, you won't want to import these transaction. You may have an
existing beancount history that accounts for the current value of the account.
This transaction would *double* the value of the account and throw it out of
balance.

You can skip these "Starting Balance" statements during the import by specifying
the command line option **--skip-starting-balances**.

# Income

(todo)

# TODO

How does income work?
the --since command line option

YNAB comes with several special accounts.

Internal Master Category:Deferred Income SubCategory
Internal Master Category:Inflows
Internal Master Category:Uncategorized

Credit Card Payments:Timo Mastercard

and off-budget accounts.
