"""Explicit demo test runner. Sends only Fuji test AVAX and MockUSDC."""
import json
import secrets
import time
import urllib.request
import os
from pathlib import Path
from eth_account import Account
from web3 import Web3
from .chain import Chain, FIELDS
from .config import Settings


def request(path, bearer, body=None, headers=None):
    req = urllib.request.Request('http://127.0.0.1:8000'+path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization':'Bearer '+bearer,'Content-Type':'application/json',**(headers or {})})
    with urllib.request.urlopen(req) as response:
        return json.load(response)


def main():
    settings = Settings()
    if not settings.allow_existing_fuji_demo or settings.chain_id != 43113:
        raise ValueError('This runner requires the explicitly enabled Fuji demo')
    chain = Chain(settings)
    chain.ready()
    w3 = chain.w3
    funding = Account.from_key(settings.signer_private_key.get_secret_value())
    buyer_file = Path('/tmp/uc-fuji-test-buyer.key')
    if buyer_file.exists():
        buyer = Account.from_key(buyer_file.read_text())
    else:
        buyer = Account.create()
        buyer_file.write_text(Web3.to_hex(buyer.key))
        buyer_file.chmod(0o600)

    def send(account, transaction):
        transaction.pop('maxFeePerGas', None)
        transaction.pop('maxPriorityFeePerGas', None)
        transaction.pop('type', None)
        transaction.update(chainId=43113, nonce=w3.eth.get_transaction_count(account.address), gasPrice=max(w3.eth.gas_price,10**9))
        if 'gas' not in transaction:
            transaction['gas'] = int(w3.eth.estimate_gas({**transaction,'from':account.address}) * 1.25)
        signed = account.sign_transaction(transaction)
        receipt = w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(signed.raw_transaction),timeout=120)
        if receipt['status'] != 1:
            raise RuntimeError('Fuji transaction reverted')
        return receipt

    if w3.eth.get_balance(funding.address) < Web3.to_wei('0.0011','ether'):
        raise ValueError('Demo funding wallet needs at least 0.0011 test AVAX')
    # Fresh buyer separates sender and recipient. No buyer key is printed or persisted.
    if w3.eth.get_balance(buyer.address) < Web3.to_wei('0.0005','ether'):
        send(funding, {'to':buyer.address,'value':Web3.to_wei('0.001','ether'),'gas':21000})
    token_abi = [
        {'type':'function','name':'mint','inputs':[{'type':'address'},{'type':'uint256'}],'outputs':[],'stateMutability':'nonpayable'},
        {'type':'function','name':'approve','inputs':[{'type':'address'},{'type':'uint256'}],'outputs':[{'type':'bool'}],'stateMutability':'nonpayable'},
        {'type':'function','name':'balanceOf','inputs':[{'type':'address'}],'outputs':[{'type':'uint256'}],'stateMutability':'view'}]
    token = w3.eth.contract(address=settings.token_address,abi=token_abi)
    draft_id = os.environ.get('TEST_SHOPIFY_DRAFT_ID')
    if draft_id:
        payment = request('/api/shopify/draft-orders/'+draft_id+'/checkout',settings.merchant_api_key.get_secret_value(),{})
        amount = int(payment['amount_units'])
    else:
        amount = 10000000
        payment = request('/api/payment-sessions',settings.merchant_api_key.get_secret_value(),
            {'order_ref':'integration-dai-'+secrets.token_hex(4),'amount_units':str(amount),'expires_in':3600},
            {'Idempotency-Key':secrets.token_hex(16)})
    if payment['status'] == 'PAID':
        print(json.dumps({'session_id':payment['id'],'status':'PAID','tx_hash':payment['tx_hash']}))
        return
    send(buyer,token.functions.mint(buyer.address,amount).build_transaction({'from':buyer.address}))
    path = '/api/payment-sessions/'+payment['id']
    auth = payment['checkout_token']
    data = request(path+'/intent',auth,{'buyer':buyer.address})
    send(buyer,token.functions.approve(settings.router_address,amount).build_transaction({'from':buyer.address}))
    abi = [{'type':'function','name':'pay','stateMutability':'nonpayable','inputs':[
        {'type':'tuple','components':[{'name':name,'type':kind} for name,kind in FIELDS]}, {'type':'bytes'}],'outputs':[]}]
    router = w3.eth.contract(address=settings.router_address,abi=abi)
    values = [data['intent'][name] for name,kind in FIELDS]
    values[4] = int(values[4])
    before = token.functions.balanceOf(settings.merchant_address).call()
    receipt = send(buyer,router.functions.pay(tuple(values),data['signature']).build_transaction({'from':buyer.address}))
    request(path+'/transactions',auth,{'tx_hash':Web3.to_hex(receipt['transactionHash'])})
    after = token.functions.balanceOf(settings.merchant_address).call()
    assert after-before == amount
    print(json.dumps({'session_id':payment['id'],'buyer':buyer.address,'merchant':settings.merchant_address,
        'tx_hash':Web3.to_hex(receipt['transactionHash']),'merchant_received_units':after-before},indent=2),flush=True)
    for attempt in range(90):
        result = request(path+'/status',auth)
        if result['status'] == 'PAID' and (not draft_id or result.get('shopify_status') == 'SYNCED'):
            print(json.dumps({'status':'PAID','shopify_status':result.get('shopify_status'),'shopify_order_id':result.get('shopify_order_id'),'session_id':payment['id'],'tx_hash':result['tx_hash'],
                'checkout_url':'http://localhost:8000/checkout#id='+payment['id']+'&token='+auth},indent=2),flush=True)
            return
        time.sleep(2)
    raise RuntimeError('Transaction succeeded but worker has not marked PAID within 180 seconds')


if __name__ == '__main__':
    main()
