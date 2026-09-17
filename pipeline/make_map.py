import json, math
t=json.load(open('build/countries-50m.json'))
sx,sy=t['transform']['scale']; tx,ty=t['transform']['translate']
arcs=[]
for a in t['arcs']:
    x=y=0; pts=[]
    for dx,dy in a:
        x+=dx;y+=dy; pts.append((x*sx+tx,y*sy+ty))
    arcs.append(pts)
def arc(i):
    return arcs[i] if i>=0 else arcs[~i][::-1]
def ring(ids):
    out=[]
    for i in ids:
        p=arc(i); out.extend(p if not out else p[1:])
    return out
LON0,LON1,LAT0,LAT1=-113,-58.5,7,28.5
W=1000
def merc(lat): return math.log(math.tan(math.pi/4+math.radians(lat)/2))
H=W*(merc(LAT1)-merc(LAT0))/math.radians(LON1-LON0)
def proj(lon,lat):
    return ((lon-LON0)/(LON1-LON0)*W, (merc(LAT1)-merc(lat))/(merc(LAT1)-merc(LAT0))*H)
paths=[]
for g in t['objects']['countries']['geometries']:
    polys=[]
    if g['type']=='Polygon': polys=[g['arcs']]
    elif g['type']=='MultiPolygon': polys=g['arcs']
    else: continue
    d=[]
    for poly in polys:
        for r in poly:
            pts=ring(r)
            if not any(LON0-5<lo<LON1+5 and LAT0-5<la<LAT1+5 for lo,la in pts): continue
            xy=[proj(lo,max(min(la,85),-85)) for lo,la in pts]
            s=[];last=None
            for x,y in xy:
                q=(round(x,1),round(y,1))
                if q!=last: s.append(q); last=q
            if len(s)<3: continue
            d.append('M'+'L'.join(f'{x:g},{y:g}' for x,y in s)+'Z')
    if d: paths.append({'id':g.get('id'),'name':g['properties'].get('name'),'d':''.join(d)})
json.dump({'w':W,'h':round(H,1),'bbox':[LON0,LON1,LAT0,LAT1],'paths':paths},open('build/basemap.json','w'))
print(len(paths), round(H,1), sum(len(p['d']) for p in paths))
print([p['name'] for p in paths])
