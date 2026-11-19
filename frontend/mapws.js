/* SUNITE map workspace core: Web-Mercator math + slippy tile canvas + polygon tracing. No dependencies. */
(function (root) {
  const TILE = 256, R = 6378137;
  const M = {
    lonLatToPx(lon, lat, z) { const s = TILE * 2 ** z, sin = Math.sin(Math.max(-85.0511, Math.min(85.0511, lat)) * Math.PI / 180);
      return [(lon + 180) / 360 * s, (0.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI)) * s]; },
    pxToLonLat(x, y, z) { const s = TILE * 2 ** z, lon = x / s * 360 - 180, n = Math.PI - 2 * Math.PI * y / s;
      return [lon, 180 / Math.PI * Math.atan(Math.sinh(n))]; },
    metresPerPx(lat, z) { return Math.cos(lat * Math.PI / 180) * 2 * Math.PI * R / (TILE * 2 ** z); },
    /* local tangent-plane approximation for live display only (server does the exact projection) */
    localM(lon, lat, lon0, lat0) { const k = Math.PI / 180; return [(lon - lon0) * k * R * Math.cos(lat0 * k), (lat - lat0) * k * R]; },
    area(ring) { if (ring.length < 3) return 0; const [lon0, lat0] = ring[0]; const p = ring.map(q => M.localM(q[0], q[1], lon0, lat0));
      let a = 0; for (let i = 0; i < p.length; i++) { const j = (i + 1) % p.length; a += p[i][0] * p[j][1] - p[j][0] * p[i][1]; } return Math.abs(a) / 2; },
    dist(a, b) { const p = M.localM(b[0], b[1], a[0], a[1]); return Math.hypot(p[0], p[1]); },
    toGeoJSON(features) { return { type: 'FeatureCollection', features: features.filter(f => f.ring.length >= 3).map(f => ({ type: 'Feature',
      properties: { role: f.role, name: f.name, ...(f.height_m != null ? { height_m: f.height_m } : {}) },
      geometry: { type: 'Polygon', coordinates: [[...f.ring, f.ring[0]]] } })) }; },
  };
  class MapView {
    constructor(canvas, opts) { Object.assign(this, { cv: canvas, ctx: canvas.getContext('2d'), z: 18, center: [78.487, 17.385], layer: opts.layer,
      cache: new Map(), features: [], draft: null, onChange: opts.onChange || (() => {}) }); }
    size() { const r = this.cv.getBoundingClientRect(), d = window.devicePixelRatio || 1; this.w = r.width; this.h = r.height;
      this.cv.width = r.width * d; this.cv.height = r.height * d; this.ctx.setTransform(d, 0, 0, d, 0, 0); this.draw(); }
    toScreen(lon, lat) { const c = M.lonLatToPx(...this.center, this.z), p = M.lonLatToPx(lon, lat, this.z); return [p[0] - c[0] + this.w / 2, p[1] - c[1] + this.h / 2]; }
    toLonLat(x, y) { const c = M.lonLatToPx(...this.center, this.z); return M.pxToLonLat(c[0] + x - this.w / 2, c[1] + y - this.h / 2, this.z); }
    tile(x, y, z) { const key = `${this.layer.url}|${z}/${x}/${y}`; if (this.cache.has(key)) return this.cache.get(key);
      const img = new Image(); img.crossOrigin = 'anonymous'; img.onload = () => this.draw(); img.onerror = () => { img.failed = true; this.draw(); };
      img.src = this.layer.url.replace('{z}', z).replace('{x}', x).replace('{y}', y); this.cache.set(key, img); return img; }
    draw() {
      const g = this.ctx, z = Math.round(this.z); g.fillStyle = '#dde3ea'; g.fillRect(0, 0, this.w, this.h);
      const c = M.lonLatToPx(...this.center, z), x0 = Math.floor((c[0] - this.w / 2) / TILE), y0 = Math.floor((c[1] - this.h / 2) / TILE);
      let failed = 0;
      for (let tx = x0; tx * TILE < c[0] + this.w / 2; tx++) for (let ty = y0; ty * TILE < c[1] + this.h / 2; ty++) {
        if (ty < 0 || ty >= 2 ** z) continue; const im = this.tile(((tx % 2 ** z) + 2 ** z) % 2 ** z, ty, z);
        const sx = tx * TILE - c[0] + this.w / 2, sy = ty * TILE - c[1] + this.h / 2;
        if (im.complete && !im.failed && im.naturalWidth) g.drawImage(im, sx, sy); else { failed += im.failed ? 1 : 0; g.strokeStyle = '#c8d0da'; g.strokeRect(sx, sy, TILE, TILE); }
      }
      const style = { roof: ['rgba(255,117,24,0.18)', '#ff7518'], obstacle: ['rgba(200,40,40,0.35)', '#c82828'], keepout: ['rgba(120,120,120,0.3)', '#555'],
                      tree: ['rgba(30,140,60,0.35)', '#1e8c3c'], building: ['rgba(90,90,160,0.35)', '#4a4aa0'] };
      for (const f of this.features.concat(this.suggestions || [], this.draft ? [this.draft] : [])) {
        if (!f.ring.length) continue; const [fill, stroke] = style[f.role] || style.roof;
        g.beginPath(); f.ring.forEach((p, i) => { const s = this.toScreen(...p); i ? g.lineTo(...s) : g.moveTo(...s); });
        if (f !== this.draft) g.closePath(); g.fillStyle = fill; g.fill(); g.strokeStyle = stroke; g.lineWidth = 2.5; g.setLineDash(f.ai ? [6, 4] : []); g.stroke(); g.setLineDash([]);
        f.ring.forEach(p => { const s = this.toScreen(...p); g.fillStyle = '#fff'; g.beginPath(); g.arc(s[0], s[1], 4, 0, 7); g.fill(); g.stroke(); });
        if (f.ring.length >= 3 && f !== this.draft) { const s = this.toScreen(...f.ring[0]); g.fillStyle = '#0b1f3f'; g.font = 'bold 12px system-ui';
          g.fillText(`${f.name} · ${M.area(f.ring).toFixed(1)} m²${f.height_m ? ' · h ' + f.height_m + ' m' : ''}`, s[0] + 6, s[1] - 8); }
      }
      g.fillStyle = 'rgba(255,255,255,.85)'; g.fillRect(0, this.h - 18, this.w, 18); g.fillStyle = '#333'; g.font = '11px system-ui';
      g.fillText(this.layer.attribution + (failed ? '  ·  tiles unavailable (offline or provider not configured)' : ''), 6, this.h - 5);
      const mpp = M.metresPerPx(this.center[1], this.z), bar = [1, 2, 5, 10, 20, 50, 100, 200].find(m => m / mpp > 60) || 200;
      g.fillStyle = '#0b1f3f'; g.fillRect(12, this.h - 34, bar / mpp, 4); g.fillText(`${bar} m`, 12, this.h - 38);
    }
  }
  root.SuniteMap = { M, MapView };
  if (typeof module !== 'undefined') module.exports = root.SuniteMap;
})(typeof window !== 'undefined' ? window : globalThis);
