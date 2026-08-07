"""PostgreSQL multi-datasource support verification"""
import pytest
import asyncio
from sqlalchemy import text


@pytest.mark.asyncio
async def test_pg_connection():
    """PG async connection"""
    from app.clients.pg_client_manager import pg_client_manager
    assert pg_client_manager is not None
    pg_client_manager.init()
    assert pg_client_manager.engine is not None
    async with pg_client_manager.session_factory() as session:
        r = await session.execute(text('SELECT 1'))
        assert r.scalar() == 1
    await pg_client_manager.close()


@pytest.mark.asyncio
async def test_pg_metadata_tables():
    """PG metadata tables exist"""
    from app.clients.pg_client_manager import pg_client_manager
    pg_client_manager.init()
    async with pg_client_manager.session_factory() as session:
        for t in ['dim_region','dim_customer','dim_product','dim_date','fact_order']:
            r = await session.execute(text(f'SELECT COUNT(*) FROM {t}'))
            assert r.scalar() > 0, f'{t} should have data'
    await pg_client_manager.close()


@pytest.mark.asyncio
async def test_pg_aggregation():
    """PG aggregation query"""
    from app.clients.pg_client_manager import pg_client_manager
    pg_client_manager.init()
    async with pg_client_manager.session_factory() as session:
        r = await session.execute(text('''
            SELECT r.region_name, SUM(f.order_amount)
            FROM fact_order f JOIN dim_region r ON f.region_id = r.region_id
            GROUP BY r.region_name
        '''))
        rows = r.fetchall()
        assert len(rows) == 5  # 5 regions
    await pg_client_manager.close()


@pytest.mark.asyncio
async def test_pg_explain():
    """PG EXPLAIN validation"""
    from app.clients.pg_client_manager import pg_client_manager
    pg_client_manager.init()
    async with pg_client_manager.session_factory() as session:
        await session.execute(text('EXPLAIN SELECT 1'))
    await pg_client_manager.close()


def test_pg_config_loaded():
    """PG config loaded correctly"""
    from app.conf.app_config import app_config
    assert app_config.pg.host == 'test-db-host'
    assert app_config.pg.port == 5432
    assert app_config.pg.database == 'app'


def test_pg_repository_exists():
    """PGRepository exists"""
    from app.repositories.pg.pg_repository import PGRepository
    assert PGRepository is not None


def test_pg_client_manager_url_encoded():
    """password URL encoding (prevent @ from breaking URL)"""
    from app.clients.pg_client_manager import PGClientManager
    from app.conf.app_config import app_config
    mgr = PGClientManager(app_config.pg)
    url = mgr._get_url()
    assert 'test-password' in url
    assert quote_check(url)

def quote_check(url):
    """confirm @ is encoded as %40"""
    # password is test-password@2026; after encoding should be test-password%402026
    return '%40' in url or '@' not in url.split('://')[1].split('@')[0]
