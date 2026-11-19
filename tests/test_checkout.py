import time
import os
import pytest
from eth_account import Account
from fastapi.testclient import TestClient
from web3 import Web3
from app.config import Settings
from app.db import Base, Payment
from app.main import create_app
from app.chain import typed_message
from app.worker import apply_event

KEY = Account.create().key
ADDR = '0x' + '11'*20
BUYER = '0x' + '22'*20


class LocalSigner:
    def sign(self,p):
        return Web3.to_hex(Account.sign_message(typed_message(p),KEY).signature)


@pytest.fixture
def setup(tmp_path):
    s = Settings(_env_file=None,database_url=os.environ.get('TEST_DATABASE_URL',f'sqlite:///{tmp_path}/test.db'),merchant_api_key='m'*32,checkout_secret='s'*32,router_address=ADDR,token_address=ADDR,merchant_address=ADDR)
    app = create_app(s,LocalSigner())
    if os.environ.get('TEST_DATABASE_URL'):
        Base.metadata.drop_all(app.state.engine)
    Base.metadata.create_all(app.state.engine)
    with TestClient(app) as client:
        yield client,app,s


def create(client, key='one',amount='10000000'):
    return client.post('/api/payment-sessions',headers={'Authorization':'Bearer '+'m'*32,'Idempotency-Key':key},json={'order_ref':'order-1','amount_units':amount})


def signed(setup):
    c,app,s = setup
    p = create(c).json()
    headers = {'Authorization':'Bearer '+p['checkout_token']}
    result = c.post(f"/api/payment-sessions/{p['id']}/intent",headers=headers,json={'buyer':BUYER})
    assert result.status_code == 200
    return p,headers,result.json()


def test_auth_and_idempotency(setup):
    c,_,_ = setup
    assert c.post('/api/payment-sessions',json={'order_ref':'x','amount_units':'1'}).status_code == 401
    first = create(c).json()
    assert create(c).json()['id'] == first['id']
    assert create(c,amount='2').status_code == 409
    assert c.get('/api/payment-sessions/'+first['id']).status_code == 401
    assert create(c,amount=str(2**256)).status_code == 422


def test_signature_and_buyer_binding(setup):
    c,app,_ = setup
    p,h,data = signed(setup)
    with app.state.sessions() as db:
        row = db.get(Payment,p['id'])
        assert Account.recover_message(typed_message(row),signature=data['signature']) == Account.from_key(KEY).address
    other = c.post(f"/api/payment-sessions/{p['id']}/intent",headers=h,json={'buyer':ADDR})
    assert other.status_code == 409
    assert c.post(f"/api/payment-sessions/{p['id']}/intent",headers=h,json={'buyer':BUYER}).json()['intent']['paymentId'] == p['payment_id']


def evidence(p):
    block_hash,tx = bytes.fromhex('aa'*32),bytes.fromhex('bb'*32)
    event = {'address':ADDR,'args':{'paymentId':bytes.fromhex(p['payment_id'][2:]),'buyer':BUYER,'merchant':ADDR,'token':ADDR,'amount':10000000},'blockHash':block_hash,'transactionHash':tx,'logIndex':0,'blockNumber':100}
    receipt = {'status':1,'to':ADDR,'blockHash':block_hash,'transactionHash':tx}
    return event,receipt,{'hash':block_hash,'timestamp':int(time.time())}


@pytest.mark.parametrize('field',['amount','buyer','merchant','token','router','receipt_status'])
def test_wrong_evidence_never_paid(setup,field):
    _,app,s = setup
    p,_,_ = signed(setup)
    e,r,b = evidence(p)
    if field == 'amount': e['args'][field] = 1
    elif field == 'router': e['address'] = BUYER
    elif field == 'receipt_status': r['status'] = 0
    else: e['args'][field] = '0x'+'33'*20
    with app.state.sessions.begin() as db:
        assert not apply_event(db,e,r,b,s)
        assert db.get(Payment,p['id']).status == 'PENDING'


def test_paid_idempotent_and_late_discovery(setup):
    c,app,s = setup
    p,h,_ = signed(setup)
    e,r,b = evidence(p)
    with app.state.sessions.begin() as db:
        row = db.get(Payment,p['id'])
        row.deadline = int(time.time())-10
        b['timestamp'] = row.deadline-1
        assert apply_event(db,e,r,b,s)
        assert apply_event(db,e,r,b,s)
    assert c.get(f"/api/payment-sessions/{p['id']}/status",headers=h).json()['status'] == 'PAID'
    assert c.post(f"/api/payment-sessions/{p['id']}/intent",headers=h,json={'buyer':BUYER}).status_code == 409


def test_expired_intent_rejected(setup):
    c,app,_ = setup
    p = create(c).json()
    with app.state.sessions.begin() as db:
        db.get(Payment,p['id']).deadline = int(time.time())-1
    h = {'Authorization':'Bearer '+p['checkout_token']}
    assert c.post(f"/api/payment-sessions/{p['id']}/intent",headers=h,json={'buyer':BUYER}).status_code == 409


def test_transaction_submission_is_only_a_hint(setup):
    from app.db import TransactionHint
    c,app,_ = setup
    p,h,_ = signed(setup)
    path = f"/api/payment-sessions/{p['id']}/transactions"
    body = {'tx_hash':'0x'+'aa'*32}
    assert c.post(path,json=body).status_code == 401
    assert c.post(path,headers=h,json={'tx_hash':'invalid'}).status_code == 422
    assert c.post(path,headers=h,json=body).status_code == 202
    assert c.post(path,headers=h,json=body).status_code == 202
    with app.state.sessions() as db:
        assert db.get(Payment,p['id']).status == 'PENDING'
        assert db.get(TransactionHint,body['tx_hash']).checked == 0


def test_submission_worker_verifies_receipt(setup):
    from types import SimpleNamespace
    from app.db import TransactionHint
    from app.worker import verify_submissions
    c,app,s = setup
    p,h,_ = signed(setup)
    e,r,b = evidence(p)
    r['blockNumber'] = 100
    path = f"/api/payment-sessions/{p['id']}/transactions"
    c.post(path,headers=h,json={'tx_hash':Web3.to_hex(e['transactionHash'])})
    eth = SimpleNamespace(chain_id=43113,block_number=103,get_block=lambda n:b,
        get_transaction_receipt=lambda tx:r,
        contract=lambda **kwargs:SimpleNamespace(events=SimpleNamespace(PaymentReceived=lambda:SimpleNamespace(process_receipt=lambda receipt,errors:[e]))))
    verify_submissions(s,app.state.sessions,SimpleNamespace(eth=eth))
    with app.state.sessions() as db:
        assert db.get(Payment,p['id']).status == 'PENDING'
    eth.block_number = 104
    verify_submissions(s,app.state.sessions,SimpleNamespace(eth=eth))
    with app.state.sessions() as db:
        assert db.get(Payment,p['id']).status == 'PAID'
        assert db.get(TransactionHint,Web3.to_hex(e['transactionHash'])).checked == 1


def test_worker_checkpoint_retry_and_reorg(setup):
    from types import SimpleNamespace
    from app.worker import scan_once
    from app.db import Cursor
    _,app,s = setup
    p,_,_ = signed(setup)
    e,r,b = evidence(p)
    s.start_block = 100
    s.confirmations = 5
    calls = []
    def logs(**kwargs):
        calls.append(kwargs)
        return [e]
    eth = SimpleNamespace(chain_id=43113,block_number=103,get_block=lambda n:b,
        get_transaction_receipt=lambda tx:r,
        contract=lambda **kwargs:SimpleNamespace(events=SimpleNamespace(PaymentReceived=lambda:SimpleNamespace(get_logs=logs))))
    w3 = SimpleNamespace(eth=eth)
    assert scan_once(s,app.state.sessions,w3) == 0
    assert not calls
    eth.block_number = 104
    def unavailable(tx): raise OSError('RPC down')
    eth.get_transaction_receipt = unavailable
    with pytest.raises(OSError): scan_once(s,app.state.sessions,w3)
    with app.state.sessions() as db:
        assert db.get(Payment,p['id']).status == 'PENDING'
        assert db.get(Cursor,f'43113:{ADDR}').next_block == 100
    eth.get_transaction_receipt = lambda tx:r
    assert scan_once(s,app.state.sessions,w3) == 1
    assert scan_once(s,app.state.sessions,w3) == 0
    with app.state.sessions() as db:
        assert db.get(Payment,p['id']).status == 'PAID'
        assert db.get(Cursor,f'43113:{ADDR}').next_block == 101
    eth.get_block = lambda n:{**b,'hash':bytes.fromhex('cc'*32)}
    with pytest.raises(RuntimeError): scan_once(s,app.state.sessions,w3)
