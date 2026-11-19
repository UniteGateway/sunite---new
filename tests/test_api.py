from fastapi.testclient import TestClient
from backend.app import app
client=TestClient(app)
SITE={'name':'Test roof','latitude':17.385,'longitude':78.487,'roof':[{'x':0,'y':0},{'x':30,'y':0},{'x':30,'y':18},{'x':0,'y':18}], 'obstacles':[{'name':'Room','height_m':2,'polygon':[{'x':8,'y':7},{'x':12,'y':7},{'x':12,'y':10},{'x':8,'y':10}]}]}
def test_health():assert client.get('/api/health').json()['status']=='ok'
def test_roof_design():
 r=client.post('/api/design',json=SITE);assert r.status_code==200,r.text
 d=r.json();assert d['panel_count']>0;assert len(d['panels'])==d['panel_count'];assert d['dc_kwp']>0
 assert len(d['shading_samples'])>0

def test_exports():
 for url in ['/api/export/boq.xlsx','/api/export/layout.dxf']:
  r=client.post(url,json=SITE);assert r.status_code==200,(url,r.text);assert len(r.content)>100

def test_invalid_polygon():
 r=client.post('/api/design',json={**SITE,'roof':[{'x':0,'y':0},{'x':1,'y':1},{'x':2,'y':2}]});assert r.status_code==422

def _login(tmp_path, monkeypatch):
 import re, sqlite3
 monkeypatch.setenv('SUNITE_DB_PATH',str(tmp_path/'sunite.sqlite3'))
 monkeypatch.setenv('SUNITE_AUTH_MODE','required')
 r=client.post('/api/auth/register',json={'org_name':'Test Co','full_name':'Tester','email':'t@test.example','password':'Correct-Horse-42'})
 assert r.status_code==201,r.text
 con=sqlite3.connect(str(tmp_path/'sunite.sqlite3'))
 tok=re.search(r'token=([\w\-]+)',con.execute("SELECT body FROM email_outbox WHERE template='verify_email'").fetchone()[0]).group(1)
 assert client.post('/api/auth/verify-email',json={'token':tok}).status_code==200
 r=client.post('/api/auth/login',json={'email':'t@test.example','password':'Correct-Horse-42'})
 return {'Authorization':'Bearer '+r.json()['token']}

def test_persistent_projects(tmp_path, monkeypatch):
 h=_login(tmp_path,monkeypatch)
 assert client.get('/api/projects').status_code==401          # no session -> rejected
 r=client.post('/api/projects',json=SITE,headers=h)
 assert r.status_code==200,r.text
 info=r.json();ident=info['id']
 assert client.get('/api/projects/'+ident,headers=h).json()['revision']==1
 assert any(p['id']==ident for p in client.get('/api/projects',headers=h).json())
 conflict=client.put('/api/projects/'+ident,json=SITE,headers={**h,'If-Match':'999'})
 assert conflict.status_code==409
 update=client.put('/api/projects/'+ident,json=SITE,headers={**h,'If-Match':'1'})
 assert update.status_code==200 and update.json()['revision']==2
 assert client.delete('/api/projects/'+ident,headers=h).status_code==200
 assert client.get('/api/projects/'+ident,headers=h).status_code==404
 assert client.get('/api/health').headers['X-Content-Type-Options']=='nosniff'

def test_electrical_and_shading_export():
 r=client.post('/api/design',json=SITE)
 assert r.status_code==200
 d=r.json()
 assert 'electrical' in d and d['electrical']['max_modules_per_string_voltage']>0
 assert 'unit_rate_inr' in d['boq'][0]
 export=client.post('/api/export/shading.csv',json=SITE)
 assert export.status_code==200 and b'shaded_panels' in export.content

def test_priced_boq():
 r=client.post('/api/design',json={**SITE,'material_rates_inr':{'PV modules':9000}})
 assert r.status_code==200
 d=r.json();assert d['priced_boq_subtotal_inr']==d['panel_count']*9000
