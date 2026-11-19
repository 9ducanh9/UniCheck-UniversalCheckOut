import logging
import time
from sqlalchemy import select, text
from web3 import Web3
from .chain import ABI, Chain, address
from .config import Settings
from .db import Cursor, Payment, TransactionHint, database
from web3.exceptions import TransactionNotFound
from web3.logs import DISCARD
from .shopify import Shopify, sync_paid


def verify_submissions(settings, sessions, w3):
    if w3.eth.chain_id != settings.chain_id:
        raise ValueError('RPC chain mismatch')
    contract = w3.eth.contract(address=address(settings.router_address),abi=ABI)
    with sessions.begin() as db:
        hints = db.scalars(select(TransactionHint).where(TransactionHint.checked == 0).limit(20).with_for_update(skip_locked=True)).all()
        for hint in hints:
            try:
                receipt = w3.eth.get_transaction_receipt(hint.tx_hash)
            except TransactionNotFound:
                continue
            if w3.eth.block_number - receipt['blockNumber'] + 1 < settings.confirmations:
                continue
            block = w3.eth.get_block(receipt['blockNumber'])
            payment = db.get(Payment,hint.session_id)
            for event in contract.events.PaymentReceived().process_receipt(receipt,errors=DISCARD):
                if Web3.to_hex(event['args']['paymentId']) == payment.payment_id:
                    apply_event(db,event,receipt,block,settings)
            hint.checked = 1

log = logging.getLogger(__name__)


def apply_event(db, event, receipt, block, settings):
    """Never accept a browser claim. Match canonical receipt and all signed fields."""
    if event.get('removed', False) or receipt['status'] != 1:
        return False
    if event['address'].lower() != settings.router_address.lower():
        return False
    if receipt['to'] is None or receipt['to'].lower() != settings.router_address.lower():
        return False
    if event['blockHash'] != block['hash'] or receipt['blockHash'] != block['hash']:
        raise ValueError('Noncanonical receipt; retry batch')
    if event['transactionHash'] != receipt['transactionHash']:
        return False
    args = event['args']
    pid = Web3.to_hex(args['paymentId'])
    p = db.scalar(select(Payment).where(Payment.payment_id == pid).with_for_update())
    if p is None or not p.signature or not p.buyer:
        return False
    if p.chain_id != settings.chain_id or p.router.lower() != event['address'].lower():
        return False
    for name in ('buyer','merchant','token'):
        if getattr(p,name).lower() != args[name].lower():
            return False
    if int(p.amount) != args['amount'] or block['timestamp'] > p.deadline:
        return False
    tx_hash = Web3.to_hex(event['transactionHash'])
    if p.status == 'PAID':
        if (p.tx_hash,p.log_index) != (tx_hash,event['logIndex']):
            raise ValueError('Conflicting payment evidence')
        return True
    p.status, p.tx_hash, p.log_index, p.block_number = 'PAID', tx_hash, event['logIndex'], event['blockNumber']
    return True


def scan_once(settings, sessions, w3):
    if w3.eth.chain_id != settings.chain_id:
        raise ValueError('RPC chain mismatch')
    key = f'{settings.chain_id}:{settings.router_address.lower()}'
    with sessions.begin() as db:
        # One active scanner per DB; lock and cursor commit atomically with payments.
        if db.bind.dialect.name == 'postgresql':
            if not db.scalar(text('SELECT pg_try_advisory_xact_lock(731143113)')):
                return 0
        cursor = db.get(Cursor,key)
        if cursor is None:
            cursor = Cursor(id=key,next_block=settings.start_block)
            db.add(cursor)
        if cursor.previous_hash and Web3.to_hex(w3.eth.get_block(cursor.next_block-1)['hash']) != cursor.previous_hash:
            raise RuntimeError('Deep reorg detected: stop and reconcile paid sessions before resuming')
        safe_head = w3.eth.block_number - settings.confirmations + 1
        end = min(safe_head,cursor.next_block+min(settings.block_batch,100)-1)
        if end < cursor.next_block:
            return 0
        end_hash = Web3.to_hex(w3.eth.get_block(end)['hash'])
        contract = w3.eth.contract(address=address(settings.router_address),abi=ABI)
        events = contract.events.PaymentReceived().get_logs(from_block=cursor.next_block,to_block=end)
        for event in events:
            receipt = w3.eth.get_transaction_receipt(event['transactionHash'])
            block = w3.eth.get_block(event['blockNumber'])
            if not apply_event(db,event,receipt,block,settings):
                log.warning('Ignored unmatched payment event tx=%s',Web3.to_hex(event['transactionHash']))
        if Web3.to_hex(w3.eth.get_block(end)['hash']) != end_hash:
            raise ValueError('Chain changed during scan; retry batch')
        cursor.next_block, cursor.previous_hash = end+1,end_hash
        return len(events)


def main():
    logging.basicConfig(level=logging.INFO)
    s = Settings()
    s.validate_runtime()
    chain = Chain(s)
    chain.ready()
    engine,sessions = database(s.database_url)
    shopify = Shopify(s) if s.shopify_shop else None
    try:
        while True:
            behind = False
            try:
                verify_submissions(s,sessions,chain.w3)
                if shopify:
                    sync_paid(s,sessions,shopify)
                count = scan_once(s,sessions,chain.w3)
                if count:
                    log.info('Processed %d events',count)
                with sessions() as db:
                    cursor = db.get(Cursor, f'{s.chain_id}:{s.router_address.lower()}')
                    behind = cursor and cursor.next_block + 100 < chain.w3.eth.block_number - s.confirmations
            except RuntimeError:
                raise
            except Exception:
                log.exception('Scan failed; cursor unchanged, retrying')
            time.sleep(1 if behind else s.poll_seconds)
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
