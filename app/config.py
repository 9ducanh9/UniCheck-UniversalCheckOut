from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    database_url: str = 'postgresql+psycopg://checkout:checkout@localhost:5432/checkout'
    merchant_api_key: SecretStr
    checkout_secret: SecretStr
    signer_private_key: SecretStr = SecretStr('')
    rpc_url: str = 'https://api.avax-test.network/ext/bc/C/rpc'
    chain_id: int = 43113
    router_address: str = ''
    token_address: str = ''
    merchant_address: str = ''
    start_block: int = 0
    confirmations: int = 5
    block_batch: int = 100
    poll_seconds: int = 5
    cors_origin: str = 'http://localhost:3000'
    allow_existing_fuji_demo: bool = False
    cart_demo_origin: str = ''
    shopify_shop: str = ''
    shopify_client_id: str = ''
    shopify_client_secret: SecretStr = SecretStr('')

    def validate_runtime(self):
        if len(self.merchant_api_key.get_secret_value()) < 32 or len(self.checkout_secret.get_secret_value()) < 32:
            raise ValueError('API key and checkout secret must each have at least 32 characters')
        if self.confirmations < 1 or self.block_batch < 1 or self.start_block < 0:
            raise ValueError('Invalid worker configuration')
