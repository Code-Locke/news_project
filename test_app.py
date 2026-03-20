import pytest

from app import app

@pytest.fixture

def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

def test_home(client):
    ans = client.get('/')
    assert ans.status_code ==200

def test_abt(client):
    ans = client.get('/about')
    assert ans.status_code==200

def test_feat(client):
    ans = client.get('/features')
    assert ans.status_code == 200
