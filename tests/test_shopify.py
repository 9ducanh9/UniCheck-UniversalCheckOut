from copy import deepcopy
from sqlalchemy import select
from app.shopify import amount_units, create_checkout, sync_paid
from app.db import Payment, ShopifyLink
from test_checkout import setup
import pytest


class Store:
    def __init__(self):
        self.data = {'id':'gid://shopify/DraftOrder/1','name':'#D1','status':'OPEN',
            'updatedAt':'2026-11-18T00:00:00Z','totalPriceSet':{'presentmentMoney':{'amount':'200.00','currencyCode':'USD'}},'order':None}
        self.calls = 0

    def draft(self,id):
        return deepcopy(self.data)

    def complete(self,id):
        self.calls += 1
        self.data.update(status='COMPLETED',order={'id':'gid://shopify/Order/2'})
        return self.data


def test_shopify_payment_sync(setup):
    _,app,s = setup
    s.shopify_shop = 'test.myshopify.com'
    store = Store()
    p = create_checkout(s,app.state.sessions,store,store.data['id'])
    assert p.amount == '200000000'
    assert create_checkout(s,app.state.sessions,store,store.data['id']).id == p.id
    sync_paid(s,app.state.sessions,store)
    assert store.calls == 0
    with app.state.sessions.begin() as db:
        db.get(Payment,p.id).status = 'PAID'
    sync_paid(s,app.state.sessions,store)
    sync_paid(s,app.state.sessions,store)
    assert store.calls == 1
    with app.state.sessions() as db:
        link = db.scalar(select(ShopifyLink))
        assert link.status == 'SYNCED'
        assert link.order_id == 'gid://shopify/Order/2'


def test_changed_order_requires_review(setup):
    _,app,s = setup
    s.shopify_shop = 'test.myshopify.com'
    store = Store()
    p = create_checkout(s,app.state.sessions,store,store.data['id'])
    with app.state.sessions.begin() as db:
        db.get(Payment,p.id).status = 'PAID'
    store.data['updatedAt'] = '2026-11-18T01:00:00Z'
    sync_paid(s,app.state.sessions,store)
    assert store.calls == 0
    with app.state.sessions() as db:
        assert db.scalar(select(ShopifyLink)).status == 'REVIEW'


def test_currency_guard():
    store = Store()
    store.data['totalPriceSet']['presentmentMoney']['currencyCode'] = 'VND'
    with pytest.raises(ValueError):
        amount_units(store.data)
