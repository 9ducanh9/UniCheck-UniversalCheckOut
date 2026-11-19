import hashlib
import json
import logging
import re
import secrets
import time
from decimal import Decimal

import httpx
from sqlalchemy import select, text
from web3 import Web3

from .chain import address
from .db import Payment, ShopifyLink

log = logging.getLogger(__name__)
DRAFT_QUERY = '''query($id: ID!) {
  draftOrder(id: $id) {
    id name status updatedAt
    totalPriceSet { presentmentMoney { amount currencyCode } }
  }
}'''


class Shopify:
    def __init__(self, settings):
        self.settings = settings
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]*\.myshopify\.com', settings.shopify_shop):
            raise ValueError('Invalid Shopify domain')
        self.base = 'https://' + settings.shopify_shop
        self.token = None
        self.expires = 0

    def graphql(self, query, variables):
        with httpx.Client(timeout=25) as client:
            if not self.token or time.time() >= self.expires:
                response = client.post(self.base + '/admin/oauth/access_token', data={
                    'grant_type':'client_credentials',
                    'client_id':self.settings.shopify_client_id,
                    'client_secret':self.settings.shopify_client_secret.get_secret_value(),
                })
                response.raise_for_status()
                auth = response.json()
                self.token = auth['access_token']
                self.expires = time.time() + auth.get('expires_in', 3600) - 60
            response = client.post(self.base + '/admin/api/2026-10/graphql.json',
                headers={'X-Shopify-Access-Token':self.token},
                json={'query':query,'variables':variables})
            response.raise_for_status()
            result = response.json()
            if result.get('errors'):
                raise ValueError('Shopify rejected query: ' + ', '.join(e.get('extensions', {}).get('code', 'ERROR') for e in result['errors']))
            return result['data']

    def draft(self, draft_id):
        result = self.graphql(DRAFT_QUERY, {'id':draft_id})['draftOrder']
        if result is None:
            raise ValueError('Draft order not found')
        return result

    def complete(self, draft_id):
        result = self.graphql('''mutation($id: ID!) {
          draftOrderComplete(id:$id, paymentPending:false) {
            draftOrder { id status }
            userErrors { field message }
          }
        }''', {'id':draft_id})['draftOrderComplete']
        if result['userErrors']:
            raise ValueError('Shopify completion rejected: ' + '; '.join(e['message'] for e in result['userErrors']))
        return result['draftOrder']

    def create_cart_draft(self, items):
        lines = [{'variantId': 'gid://shopify/ProductVariant/' + item.variant_id,
                  'quantity': item.quantity} for item in items]
        result = self.graphql('''mutation($input: DraftOrderInput!) {
          draftOrderCreate(input: $input) {
            draftOrder { id }
            userErrors { message }
          }
        }''', {'input': {'lineItems': lines, 'tags': ['unicheck-fuji-demo'],
                        'presentmentCurrencyCode': 'USD'}})['draftOrderCreate']
        if result['userErrors'] or not result['draftOrder']:
            raise ValueError('Shopify rejected cart variants')
        return result['draftOrder']['id']


def amount_units(draft):
    money = draft['totalPriceSet']['presentmentMoney']
    if money['currencyCode'] != 'USD':
        raise ValueError('Demo supports USD orders only')
    units = Decimal(money['amount']) * 1000000
    if not units.is_finite() or units <= 0 or units != units.to_integral_value() or units >= 2**256:
        raise ValueError('Unsupported order amount')
    return str(int(units))


def snapshot(draft):
    data = [draft['id'], draft['updatedAt'], draft['totalPriceSet']]
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def create_checkout(settings, sessions, client, draft_id):
    # Stable one-session mapping per draft, serialized in PostgreSQL.
    with sessions.begin() as db:
        if db.bind.dialect.name == 'postgresql':
            db.execute(text('SELECT pg_advisory_xact_lock(731143114)'))
        link = db.get(ShopifyLink,draft_id)
        if link:
            payment = db.get(Payment,link.session_id)
            if payment.status != 'PAID' and payment.deadline < time.time():
                raise ValueError('Existing checkout expired; reconcile this draft before retrying')
            return payment
        draft = client.draft(draft_id)
        if draft['status'] != 'OPEN':
            raise ValueError('Draft must be OPEN')
        units = amount_units(draft)
        session_id = secrets.token_hex(16)
        payment = Payment(id=session_id,payment_id=Web3.to_hex(Web3.keccak(text=session_id)),
            idempotency_key='shopify:'+draft_id,request_hash=snapshot(draft),order_ref=draft['name'],
            merchant=address(settings.merchant_address),token=address(settings.token_address),
            router=address(settings.router_address),chain_id=settings.chain_id,amount=units,
            deadline=int(time.time())+3600)
        db.add(payment)
        db.add(ShopifyLink(draft_id=draft_id,shop=settings.shopify_shop,session_id=session_id,snapshot=snapshot(draft)))
        return payment


def sync_paid(settings, sessions, client):
    with sessions.begin() as db:
        if db.bind.dialect.name == 'postgresql':
            if not db.scalar(text('SELECT pg_try_advisory_xact_lock(731143115)')):
                return
        links = db.scalars(select(ShopifyLink).join(Payment,Payment.id == ShopifyLink.session_id)
            .where(Payment.status == 'PAID',ShopifyLink.status.in_(['WAITING','RETRY']))
            .with_for_update(skip_locked=True)).all()
        for link in links:
            if link.shop != settings.shopify_shop:
                continue
            try:
                draft = client.draft(link.draft_id)
                if draft['status'] == 'COMPLETED':
                    # A previous completion may have succeeded before a timeout.
                    # Reconcile manually rather than attribute an external completion.
                    link.status = 'REVIEW'
                    link.error = 'Draft already completed; reconcile order and payment evidence'
                    continue
                if draft['status'] != 'OPEN' or snapshot(draft) != link.snapshot:
                    link.status = 'REVIEW'
                    link.error = 'Draft changed after checkout creation'
                    continue
                result = client.complete(link.draft_id)
                if result['status'] != 'COMPLETED':
                    raise ValueError('Shopify returned incomplete result')
                if result.get('order'):
                    link.order_id = result['order']['id']
                link.status, link.error = 'SYNCED', None
                log.info('Shopify sync complete draft=%s order=%s', link.draft_id, link.order_id)
            except Exception as error:
                link.status = 'RETRY'
                # Avoid storing HTTP details that might contain credentials.
                link.error = type(error).__name__
                log.warning('Shopify sync failed draft=%s error=%s',link.draft_id,type(error).__name__)
