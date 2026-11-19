from eth_account import Account
from eth_account.messages import encode_typed_data
from web3 import Web3

FIELDS = [('paymentId','bytes32'),('buyer','address'),('merchant','address'),('token','address'),('amount','uint256'),('deadline','uint256')]
TYPES = {'PaymentIntent': [{'name': n, 'type': t} for n,t in FIELDS]}
ABI = [
    {'type':'event','name':'PaymentReceived','anonymous':False,'inputs':[
        {'name':n,'type':t,'indexed':i < 3} for i,(n,t) in enumerate(FIELDS[:5])]},
    *[{'type':'function','name':n,'stateMutability':'view','inputs':[], 'outputs':[{'type':'address'}]} for n in ['backendSigner','owner']],
    {'type':'function','name':'allowedTokens','stateMutability':'view','inputs':[{'type':'address'}],'outputs':[{'type':'bool'}]},
]


def address(value):
    if not Web3.is_address(value) or int(value, 16) == 0:
        raise ValueError('A nonzero EVM address is required')
    return Web3.to_checksum_address(value)


def intent(payment):
    return dict(zip([n for n,t in FIELDS], [payment.payment_id, payment.buyer, payment.merchant, payment.token, int(payment.amount), payment.deadline]))


def typed_message(payment):
    return encode_typed_data(domain_data={'name':'UniversalCheckout','version':'1','chainId':payment.chain_id,'verifyingContract':payment.router}, message_types=TYPES, message_data=intent(payment))


class Chain:
    def __init__(self, settings):
        self.settings = settings
        self.w3 = Web3(Web3.HTTPProvider(settings.rpc_url, request_kwargs={'timeout':20}))

    def ready(self):
        s = self.settings
        router = address(s.router_address)
        token = address(s.token_address)
        signer = Account.from_key(s.signer_private_key.get_secret_value()).address
        # This address's key was publicly exposed in the handed-over demo.
        compromised = '0x18baca02e64f7cdd9b888f9d2d67786bc0a15bc1'
        contract = self.w3.eth.contract(address=router, abi=ABI)
        if self.w3.eth.chain_id != s.chain_id or not self.w3.eth.get_code(router):
            raise ValueError('Wrong chain or Router has no code')
        owner = contract.functions.owner().call()
        demo_allowed = (s.allow_existing_fuji_demo and s.chain_id == 43113
            and router.lower() == '0xc83fa9cecf7fc125dbec893402c6f0f5e3f5aee9'
            and token.lower() == '0xa61abc464034161b4c5abaee7298c1503cf6e199')
        if (signer.lower() == compromised or owner.lower() == compromised) and not demo_allowed:
            raise ValueError('Replace compromised demo owner and signer before integration')
        if contract.functions.backendSigner().call().lower() != signer.lower():
            raise ValueError('Backend key does not match Router signer')
        if not contract.functions.allowedTokens(token).call():
            raise ValueError('Token is not allowed')
        return signer

    def sign(self, payment):
        self.ready()
        return Web3.to_hex(Account.sign_message(typed_message(payment), self.settings.signer_private_key.get_secret_value()).signature)
