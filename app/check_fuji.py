import json
import secrets
import urllib.request
from eth_account.messages import _hash_eip191_message
from web3 import Web3
from .config import Settings
from .chain import Chain, FIELDS, typed_message
from .db import Payment, database

def post(path,body,bearer,extra=None):
    r=urllib.request.Request('http://127.0.0.1:8000'+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+bearer,**(extra or {})})
    with urllib.request.urlopen(r) as response: return json.load(response)

if __name__=='__main__':
    s=Settings(); c=Chain(s); signer=c.ready()
    p=post('/api/payment-sessions',{'order_ref':'signature-check','amount_units':'10000000'},s.merchant_api_key.get_secret_value(),{'Idempotency-Key':secrets.token_hex(16)})
    data=post('/api/payment-sessions/'+p['id']+'/intent',{'buyer':signer},p['checkout_token'])
    abi=[{'name':'getPaymentIntentDigest','type':'function','stateMutability':'view','inputs':[{'name':'intent','type':'tuple','components':[{'name':n,'type':t} for n,t in FIELDS]}],'outputs':[{'type':'bytes32'}]}]
    contract=c.w3.eth.contract(address=s.router_address,abi=abi)
    values=[data['intent'][n] for n,t in FIELDS];values[4]=int(values[4])
    onchain=contract.functions.getPaymentIntentDigest(tuple(values)).call()
    engine,sessions=database(s.database_url)
    with sessions() as db:
        local=_hash_eip191_message(typed_message(db.get(Payment,p['id'])))
    assert onchain==local, 'EIP712 digest mismatch'
    print('PASS: API-issued intent digest matches deployed Fuji Router. No transaction sent.')
    engine.dispose()
