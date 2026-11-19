from sqlalchemy import create_engine, String, Integer, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class Payment(Base):
    __tablename__ = 'payment_sessions'
    __table_args__ = (UniqueConstraint('idempotency_key'),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    payment_id: Mapped[str] = mapped_column(String(66), unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    order_ref: Mapped[str] = mapped_column(String(128))
    merchant: Mapped[str] = mapped_column(String(42))
    token: Mapped[str] = mapped_column(String(42))
    router: Mapped[str] = mapped_column(String(42))
    chain_id: Mapped[int] = mapped_column(Integer)
    amount: Mapped[str] = mapped_column(String(78))
    deadline: Mapped[int] = mapped_column(Integer)
    buyer: Mapped[str | None] = mapped_column(String(42))
    signature: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default='PENDING')
    tx_hash: Mapped[str | None] = mapped_column(String(66))
    log_index: Mapped[int | None] = mapped_column(Integer)
    block_number: Mapped[int | None] = mapped_column(Integer)


class Cursor(Base):
    __tablename__ = 'worker_cursors'
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    next_block: Mapped[int] = mapped_column(Integer)
    previous_hash: Mapped[str | None] = mapped_column(String(66))


class TransactionHint(Base):
    __tablename__ = 'transaction_hints'
    tx_hash: Mapped[str] = mapped_column(String(66), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    checked: Mapped[int] = mapped_column(Integer, default=0)


class ShopifyLink(Base):
    __tablename__ = 'shopify_links'
    draft_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    shop: Mapped[str] = mapped_column(String(255))
    session_id: Mapped[str] = mapped_column(String(64), unique=True)
    snapshot: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default='WAITING')
    order_id: Mapped[str | None] = mapped_column(String(100))
    error: Mapped[str | None] = mapped_column(Text)


def database(url):
    engine = create_engine(url, pool_pre_ping=True)
    return engine, sessionmaker(engine, expire_on_commit=False)
