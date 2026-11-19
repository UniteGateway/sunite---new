const { M } = require('../frontend/mapws.js'); const assert = require('assert');
assert.deepStrictEqual(M.lonLatToPx(0, 0, 0).map(v => Math.round(v * 1e6) / 1e6), [128, 128]);   // world centre
const p = M.lonLatToPx(78.487, 17.385, 17), q = M.pxToLonLat(...p, 17);
assert.ok(Math.abs(q[0] - 78.487) < 1e-9 && Math.abs(q[1] - 17.385) < 1e-9, 'round trip');
assert.strictEqual(Math.floor(p[0] / 256), 94112, 'Hyderabad tile x at z17');   // (78.487+180)/360*2^17 = 94112.2
assert.ok(Math.abs(M.metresPerPx(0, 0) - 156543.034) < 0.01, 'equator resolution z0');
// a 30 m x 20 m rectangle traced in lon/lat measures ~600 m2
const k = 180 / Math.PI / 6378137, lat0 = 17.385, lon0 = 78.487, dlon = 30 * k / Math.cos(lat0 * Math.PI / 180), dlat = 20 * k;
const ring = [[lon0, lat0], [lon0 + dlon, lat0], [lon0 + dlon, lat0 + dlat], [lon0, lat0 + dlat]];
assert.ok(Math.abs(M.area(ring) - 600) < 0.5, 'area');
assert.ok(Math.abs(M.dist(ring[0], ring[1]) - 30) < 0.01, 'distance');
const fc = M.toGeoJSON([{ role: 'roof', name: 'A', ring }, { role: 'obstacle', name: 'T', ring: ring.slice(0, 2) }]);
assert.strictEqual(fc.features.length, 1); const c = fc.features[0].geometry.coordinates[0];
assert.deepStrictEqual(c[0], c[c.length - 1], 'closed ring');
console.log('map workspace tests passed');
