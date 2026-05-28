import pytest
import asyncpg


@pytest.mark.integration
async def test_db_pool_connects(lab_db_url):
    pool = await asyncpg.create_pool(lab_db_url, min_size=1, max_size=2)
    async with pool.acquire() as conn:
        val = await conn.fetchval("SELECT 1")
    assert val == 1
    await pool.close()
