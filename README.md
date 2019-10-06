# beancount-ynab5
 Import YNAB5 transactions into beancount, a plaintext accounting system.

# What it does.

*beancount-ynab5* will import **cleared** transactions from your cloud-based
YNAB5 budget into a beancount file. This allows you to use YNAB5 for its
budgeting, automatically pulling transactions from various US financial institutions,
and mobile app. But you can also sync it to beancount, which has support for
multiple currencies, investments, and other things.

Why only cleared transactions? This importer doesn't handle updating a transaction
that you've already imported. By waiting until the transaction is cleared we (hopefully)
avoid situations where we import something that then later gets updated.

# How to make it work.

## Get a Personal Access Token for your YNAB account.

Instructions on how to do this are on [YNAB's API website.](https://api.youneedabudget.com/)

The short version: Go to "My Account" then click on "Developer Settings".

Write down the very long token somewhere. You'll need it later.

## Automatic account mapping

Caveats:
- punctuation gets dropped
- spaces get turned into hyphen '-'

In the default category list that means

*Rent & Mortgage* in the group *Immediate Obligations* becomes
*Immediate-Obligations:RentMortgage*

A few to watch out for:

# Immediate-Obligations:RentMortgage (*Rent & Mortgage*)
# True-Expenses:RentersHome-Insurance (*Renter's/Home Insurance*)
# Immediate-Obligations:Interest--Fees (*Interest & Fees*)

## Add a ynab-id to your beancount accounts.

We need some way to map between YNAB5 accounts & budget categories and
beancount accounts. In lieu of anything smart & fancy, you need to add metadata
to all of your beancount accounts to make this happen.

Every YNAB account & budget category has a UUID identifying it. We'll use that
to tie things together.

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

### Special Accounts

YNAB comes with several special accounts.

Internal Master Category:Deferred Income SubCategory
Internal Master Category:Inflows
Internal Master Category:Uncategorized

Credit Card Payments:Timo Mastercard

and off-budget accounts.
