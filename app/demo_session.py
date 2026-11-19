"""Create a fixed 10 MockUSDC session through the merchant API, without exposing its API key."""
import json
import secrets
import urllib.request
from .config import Settings

if __name__ == '__main__':
    s = Settings()
    request = urllib.request.Request('http://127.0.0.1:8000/api/payment-sessions',
        data=json.dumps({'order_ref':'fuji-demo-'+secrets.token_hex(4),'amount_units':'10000000','expires_in':3600}).encode(),
        headers={'Content-Type':'application/json','Authorization':'Bearer '+s.merchant_api_key.get_secret_value(),'Idempotency-Key':secrets.token_hex(16)})
    with urllib.request.urlopen(request) as response:
        p = json.load(response)
    print('http://localhost:8000/checkout#id='+p['id']+'&token='+p['checkout_token'])
