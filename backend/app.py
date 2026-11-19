"""SUNITE Solar Designer: preliminary GIS-independent planar roof design API.
Coordinates are local roof metres; site latitude/longitude drives solar-position approximation.
"""
from __future__ import annotations
import io, json, math, os, sqlite3, uuid, contextlib, logging
from datetime import datetime, timezone
from typing import List, Literal
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from shapely.geometry import Polygon, box, Point
from shapely.affinity import rotate, translate
from shapely.ops import unary_union
import ezdxf
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

ROOT=os.path.dirname(os.path.dirname(__file__))
from backend.engine.pipeline import run_design, legacy_to_request, legacy_response, ENGINE_VERSION
from backend.engine.site import run_site
from backend.engine.exports import boq_xlsx, design_report_pdf
from backend.engine.energy import parse_weather_csv, fetch_pvgis_tmy
log=logging.getLogger('sunite')
app=FastAPI(title='SUNITE AI Solar Designer',version=ENGINE_VERSION,description='Preliminary module layouts, 3-D shading, string design, energy and traceable BOQ')
app.mount('/static',StaticFiles(directory=os.path.join(ROOT,'frontend')),name='static')

class Coord(BaseModel):
    x:float
    y:float
class Obstacle(BaseModel):
    name:str='Obstacle'
    polygon:List[Coord]=Field(max_length=200)
    height_m:float=Field(default=1.0,ge=0,le=100)
class Site(BaseModel):
    name:str='New Solar Project'
    latitude:float=Field(default=17.385,ge=-90,le=90)
    longitude:float=Field(default=78.487,ge=-180,le=180)
    roof:List[Coord]=Field(max_length=500)
    obstacles:List[Obstacle]=Field(default=[],max_length=300)
    tilt_deg:float=Field(default=0,ge=0,le=60)
    module_w:float=Field(default=1.134,gt=0,le=5)
    module_h:float=Field(default=2.278,gt=0,le=5)
    module_watts:int=Field(default=550,ge=100,le=1000)
    roof_rotation_deg:float=Field(default=0,ge=-180,le=180)
    walkway_m:float=Field(default=0.6,ge=0,le=10)
    perimeter_m:float=Field(default=0.5,ge=0,le=10)
    row_gap_m:float=Field(default=0.25,ge=0,le=10)
    energy_yield_kwh_per_kwp:float=Field(default=1550,ge=0,le=3000)
    electricity_rate_inr:float=Field(default=9,ge=0)
    installed_price_inr_per_w:float=Field(default=42,ge=0)
    module_voc:float=Field(default=49.9,gt=0,le=100)
    module_vmp:float=Field(default=41.7,gt=0,le=100)
    module_isc:float=Field(default=14,gt=0,le=40)
    voc_temp_coefficient_pct:float=Field(default=-0.28,ge=-1,le=0)
    minimum_design_temp_c:float=Field(default=5,ge=-50,le=50)
    maximum_cell_temp_c:float=Field(default=70,ge=20,le=100)
    inverter_max_dc_v:float=Field(default=1100,ge=200,le=2000)
    inverter_mppt_min_v:float=Field(default=200,ge=50,le=1500)
    dc_ac_ratio:float=Field(default=1.2,ge=0.8,le=2.0)
    inverter_location:Coord|None=None
    ac_connection_location:Coord|None=None
    dc_cable_route_factor:float=Field(default=1.2,ge=1,le=5)
    ac_cable_route_factor:float=Field(default=1.2,ge=1,le=5)
    material_rates_inr:dict[str,float]={}


def poly(coords):
    if len(coords)<3: raise HTTPException(422,'Polygon needs at least three coordinates')
    p=Polygon([(v.x,v.y) for v in coords])
    if not p.is_valid or p.area<=0:raise HTTPException(422,'Invalid/self-intersecting polygon')
    return p

def sun_position_approx(lat,lon,when):
    """Approximate solar altitude/azimuth (azimuth from North, clockwise); UTC input.
    Prefer pvlib when installed. This NOAA-like fallback is NOT bankable shading analysis.
    """
    try:
        import pvlib
        p=pvlib.solarposition.get_solarposition(when,lat,lon).iloc[0]
        return float(p['apparent_elevation']),float(p['azimuth'])
    except ImportError:
        n=when.timetuple().tm_yday
        h=when.hour+when.minute/60
        g=2*math.pi/365*(n-1+(h-12)/24)
        eq=229.18*(0.000075+0.001868*math.cos(g)-0.032077*math.sin(g)-0.014615*math.cos(2*g)-0.040849*math.sin(2*g))
        dec=0.006918-0.399912*math.cos(g)+0.070257*math.sin(g)-0.006758*math.cos(2*g)+0.000907*math.sin(2*g)-0.002697*math.cos(3*g)+0.00148*math.sin(3*g)
        ha=math.radians((h*60+eq+4*lon)/4-180)
        phi=math.radians(lat)
        alt=math.asin(max(-1,min(1,math.sin(phi)*math.sin(dec)+math.cos(phi)*math.cos(dec)*math.cos(ha))))
        az=math.atan2(math.sin(ha),math.cos(ha)*math.sin(phi)-math.tan(dec)*math.cos(phi))
        return math.degrees(alt),(math.degrees(az)+180)%360

def shade_polygons(site:Site, date='2026-03-21',hour_utc=6):
    dt=datetime.fromisoformat(date+'T00:00:00+00:00').replace(hour=hour_utc)
    alt,az=sun_position_approx(site.latitude,site.longitude,dt)
    shapes=[]
    if alt>3:
        for o in site.obstacles:
            p=poly(o.polygon); length=min(200,o.height_m/math.tan(math.radians(alt)))
            dx=-length*math.sin(math.radians(az));dy=-length*math.cos(math.radians(az))
            shifted=translate(p,xoff=dx,yoff=dy)
            shapes.append(unary_union([p,shifted,p.union(shifted).convex_hull]))
    return alt,az,unary_union(shapes) if shapes else Polygon()

def coords_of(g):
    if g.is_empty:return []
    if g.geom_type=='Polygon':return [[round(x,3),round(y,3)] for x,y in g.exterior.coords]
    return []

def design(site:Site):
    """v0.2-compatible response computed by the v0.3 engine."""
    payload=site.model_dump()
    try:
        return legacy_response(payload,run_design(legacy_to_request(payload)))
    except ValueError as e:
        raise HTTPException(422,str(e))

@app.get('/')
def index():
    return HTMLResponse(open(os.path.join(ROOT,'frontend','index.html'),encoding='utf-8').read())
@app.get('/equipment')
def equipment_page():
    return HTMLResponse(open(os.path.join(ROOT,'frontend','equipment.html'),encoding='utf-8').read())
@app.get('/assistant')
def assistant_page():
    return HTMLResponse(open(os.path.join(ROOT,'frontend','assistant.html'),encoding='utf-8').read())
@app.get('/map')
def map_page():
    return HTMLResponse(open(os.path.join(ROOT,'frontend','map.html'),encoding='utf-8').read())

@app.get('/api/config/map')
def map_config():
    """Tile layers come from configuration so imagery licences are chosen deliberately, never hard-coded."""
    layers=[{'name':'Street map','url':os.environ.get('SUNITE_TILE_STREET_URL','https://tile.openstreetmap.org/{z}/{x}/{y}.png'),
             'attribution':os.environ.get('SUNITE_TILE_STREET_ATTRIBUTION','© OpenStreetMap contributors'),
             'note':'OpenStreetMap tiles are for light use only (OSM tile usage policy); use a commercial provider in production.'}]
    if os.environ.get('SUNITE_TILE_SATELLITE_URL'):
        layers.insert(0,{'name':'Satellite','url':os.environ['SUNITE_TILE_SATELLITE_URL'],
                         'attribution':os.environ.get('SUNITE_TILE_SATELLITE_ATTRIBUTION','Imagery provider'),
                         'note':'Satellite imagery licensed under your provider agreement.'})
    return {'layers':layers,'satellite_configured':bool(os.environ.get('SUNITE_TILE_SATELLITE_URL'))}

@app.get('/viewer')
def viewer_page():
    return HTMLResponse(open(os.path.join(ROOT,'frontend','viewer.html'),encoding='utf-8').read())
@app.get('/operations')
def operations_page():
    return HTMLResponse(open(os.path.join(ROOT,'frontend','operations.html'),encoding='utf-8').read())
@app.get('/verify')
def verify_page():return index()
@app.get('/reset')
def reset_page():return index()
@app.get('/accept-invite')
def invite_page():return index()
@app.get('/api/health')
def health():return {'status':'ok','version':ENGINE_VERSION}
@app.post('/api/design')
def create_design(site:Site):return design(site)
@app.post('/api/export/boq.xlsx')
def boq_excel(site:Site):
    d=design(site);wb=Workbook();ws=wb.active;ws.title='BOQ'
    ws.append(['SUNITE AI SOLAR DESIGNER — PRELIMINARY BOQ']);ws.append(['Project',site.name]);ws.append(['DC capacity (kWp)',d['dc_kwp']]);ws.append(['Disclaimer','Quantities must be validated against approved engineering drawings']);ws.append([])
    ws.append(['Item','Unit','Quantity','Unit rate INR','Extended INR','Notes'])
    for a in d['boq']:ws.append([a['item'],a['unit'],a['qty'],a['unit_rate_inr'],a['amount_inr'],a['note']])
    for col in ['A','B','C','D','E','F']:ws.column_dimensions[col].width={'A':38,'B':17,'C':18,'D':20,'E':20,'F':65}[col]
    for c in ws[6]:c.font=Font(color='FFFFFF',bold=True);c.fill=PatternFill('solid',fgColor='12365A')
    buf=io.BytesIO();wb.save(buf);buf.seek(0)
    return StreamingResponse(buf,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':'attachment; filename="sunite-boq.xlsx"'})
@app.post('/api/export/layout.dxf')
def layout_dxf(site:Site):
    d=design(site);doc=ezdxf.new();m=doc.modelspace();doc.layers.new('ROOF',dxfattribs={'color':7});doc.layers.new('MODULES',dxfattribs={'color':3});doc.layers.new('OBSTACLES',dxfattribs={'color':1})
    m.add_lwpolyline(d['roof'],close=True,dxfattribs={'layer':'ROOF'})
    for p in d['panels']:m.add_lwpolyline(p,close=True,dxfattribs={'layer':'MODULES'})
    for o in d['obstacles']:m.add_lwpolyline(o['polygon'],close=True,dxfattribs={'layer':'OBSTACLES'})
    stream=io.StringIO();doc.write(stream);out=io.BytesIO(stream.getvalue().encode('utf-8'))
    return StreamingResponse(out,media_type='application/dxf',headers={'Content-Disposition':'attachment; filename="sunite-layout.dxf"'})


# Project persistence: tenant-scoped store (backend/services/projects.py). Same paths and response
# shapes as v0.2; now requires a session unless SUNITE_AUTH_MODE=local_demo (refused in production).
from fastapi import Request
from backend.api import identity_routes as ID
from backend.services import projects as PRJ
from backend.core.config import get_settings
from backend.core import logging as slog
get_settings()                       # fail fast on invalid configuration
ID.register(app)
from backend.api import catalog_geo_routes as CG
CG.register(app)
from backend.api import business_routes as BR
BR.register(app)
BR.register_reports(app)
BR.register_more_reports(app)
BR.register_ai(app)
BR.register_ai_vision(app)
BR.register_layout_edit(app)

@app.middleware('http')
async def security_and_request_id(request, call_next):
    slog.request_id.set(request.headers.get('x-request-id') or uuid.uuid4().hex[:16])
    response=await call_next(request)
    response.headers['X-Request-ID']=slog.request_id.get()
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['Cache-Control']=response.headers.get('Cache-Control','no-store')
    response.headers['Content-Security-Policy']=("default-src 'self'; img-src 'self' data: https://*.openstreetmap.org; "
        "frame-src https://www.openstreetmap.org; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")
    return response

@app.get('/api/projects')
def list_projects(request:Request,authorization:str|None=Header(default=None)):
    with ID.db() as con:
        rows=ID.run(lambda:PRJ.list_projects(con,ID.principal(con,authorization,request)))
    return [{k:r[k] for k in ('id','name','revision','updated_utc')} for r in rows]

@app.post('/api/projects')
def save_project(site:Site,request:Request,authorization:str|None=Header(default=None)):
    design(site)
    with ID.db() as con:
        return ID.run(lambda:PRJ.create(con,ID.principal(con,authorization,request),name=site.name,
                                        payload=site.model_dump(),engine_version=ENGINE_VERSION))

@app.get('/api/projects/{project_id}')
def get_project(project_id:str,request:Request,authorization:str|None=Header(default=None)):
    with ID.db() as con:
        return ID.run(lambda:PRJ.get(con,ID.principal(con,authorization,request),project_id))

@app.put('/api/projects/{project_id}')
def update_project(project_id:str,site:Site,request:Request,if_match:int|None=Header(default=None),authorization:str|None=Header(default=None)):
    design(site)
    with ID.db() as con:
        return ID.run(lambda:PRJ.update(con,ID.principal(con,authorization,request),project_id,payload=site.model_dump(),
                                        if_match=if_match,name=site.name,engine_version=ENGINE_VERSION))

@app.delete('/api/projects/{project_id}')
def delete_project(project_id:str,request:Request,authorization:str|None=Header(default=None)):
    with ID.db() as con:
        ID.run(lambda:PRJ.delete(con,ID.principal(con,authorization,request),project_id))
    return {'deleted':True}

@app.post('/api/export/shading.csv')
def shading_csv(site:Site):
    import csv
    d=design(site);out=io.StringIO();w=csv.DictWriter(out,fieldnames=['date','utc_hour','altitude_deg','azimuth_deg','shaded_panels','shade_pct'])
    w.writeheader();w.writerows(d['shading_samples'])
    return StreamingResponse(io.BytesIO(out.getvalue().encode()),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="sunite-shading.csv"'})


# ---------------------------------------------------------------- v0.3 API
MAX_WEATHER_BYTES=3_000_000
_pvgis_cache:dict={}

class V3Request(BaseModel):
    """Full engine request. Structure documented in docs/API_V3.md; values validated by the engine."""
    site:dict
    module:dict
    layout:dict={}
    inverter:dict|None=None
    temps:dict|None=None
    shading:dict={}
    cables:dict={}
    pricing:dict={}
    finance:dict|None=None
    losses_pct:dict|None=None
    degradation_pct:float|None=None
    weather_source:str|None=Field(default=None,description="'pvgis' to fetch PVGIS TMY (internet required)")
    weather_csv:str|None=Field(default=None,max_length=MAX_WEATHER_BYTES)
    module_id:str|None=Field(default=None,description='Catalogue id; overrides inline module specs (sign-in required)')
    inverter_id:str|None=Field(default=None,description='Catalogue id; overrides inline inverter specs (sign-in required)')

def _weather(req:V3Request):
    if req.weather_csv:
        try:return parse_weather_csv(req.weather_csv),'user CSV'
        except ValueError as e:raise HTTPException(422,f'Weather CSV rejected: {e}')
    if req.weather_source=='pvgis':
        key=(round(req.site['latitude'],3),round(req.site['longitude'],3))
        if key not in _pvgis_cache:
            try:_pvgis_cache[key]=fetch_pvgis_tmy(*key)
            except Exception as e:
                log.warning('PVGIS fetch failed: %s',e)
                raise HTTPException(503,'PVGIS weather service unavailable; upload a weather CSV instead. No energy figures were generated.')
        return _pvgis_cache[key],'PVGIS TMY (EU JRC)'
    return None,None

def _resolve_equipment(req:V3Request,authorization,request):
    body=req.model_dump(exclude={'weather_csv','module_id','inverter_id'})
    if req.module_id or req.inverter_id:
        from backend.services import equipment as EQ
        with ID.db() as con:
            p=ID.principal(con,authorization,request) if authorization or get_settings().auth_mode=='local_demo' else None
            if p is None:raise HTTPException(401,'Sign in to use catalogue equipment',headers={'WWW-Authenticate':'Bearer'})
            if req.module_id:
                spec=ID.run(lambda:EQ.for_design(con,p,req.module_id,'module'))
                body['module']={**spec,'length_m':spec['length_m'],'width_m':spec['width_m']}
            if req.inverter_id:
                body['inverter']=ID.run(lambda:EQ.for_design(con,p,req.inverter_id,'inverter'))
    return body

def _run_v3(req:V3Request,authorization=None,request=None):
    roofs=req.site.get('roofs') or [{'polygon':req.site.get('roof',[])}]
    if any(len(r.get('polygon',[]))>500 for r in roofs):raise HTTPException(422,'Roof polygons limited to 500 vertices')
    w,src=_weather(req)
    body=_resolve_equipment(req,authorization,request)
    if src:body['weather_source']=src
    try:return run_site({k:v for k,v in body.items() if v is not None},weather=w)
    except (ValueError,KeyError,TypeError) as e:
        raise HTTPException(422,f'Invalid design request: {e}')

@app.post('/api/v3/design')
def design_v3(req:V3Request,request:Request=None,authorization:str|None=Header(default=None)):
    return _run_v3(req,authorization,request)

def _single(req,authorization,request):
    r=_run_v3(req,authorization,request)
    if r.get('multi_roof'):raise HTTPException(422,'Report/BOQ export currently supports one roof per request; export each roof separately')
    return r

@app.post('/api/v3/export/boq.xlsx')
def boq_v3(req:V3Request,request:Request=None,authorization:str|None=Header(default=None)):
    return StreamingResponse(io.BytesIO(boq_xlsx(_single(req,authorization,request))),media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':'attachment; filename="sunite-boq.xlsx"'})

@app.post('/api/v3/export/report.pdf')
def report_v3(req:V3Request,request:Request=None,authorization:str|None=Header(default=None)):
    return StreamingResponse(io.BytesIO(design_report_pdf(_single(req,authorization,request))),media_type='application/pdf',headers={'Content-Disposition':'attachment; filename="sunite-design-report.pdf"'})

@app.post('/api/v3/sun-path')
def sun_path(body:dict):
    from backend.engine.solar import sun_path_day
    try:return sun_path_day(float(body['latitude']),float(body['longitude']),str(body.get('date','2026-12-21'))[:10],
                            max(5,min(120,int(body.get('step_min',30)))),float(body.get('min_elev',0)))
    except (KeyError,ValueError,TypeError) as e:raise HTTPException(422,f'Invalid sun-path request: {e}')

@app.post('/api/export/report.pdf')
def report_legacy(site:Site):
    payload=site.model_dump()
    try:r=run_design(legacy_to_request(payload))
    except ValueError as e:raise HTTPException(422,str(e))
    return StreamingResponse(io.BytesIO(design_report_pdf(r)),media_type='application/pdf',headers={'Content-Disposition':'attachment; filename="sunite-design-report.pdf"'})
