// node tests/viewer3d.test.js  - geometry checks for the 3-D viewer
const V = require('../frontend/viewer3d.js'); const assert = require('assert');
const base = { target: [0, 0, 0], yaw: 0, scale: 10, perspective: false };
const top = V.makeProjector({ ...base, ...V.PRESETS.top }, 200, 200);
assert.deepStrictEqual(top([0, 0, 0]).slice(0, 2), [100, 100]);
assert.ok(top([1, 0, 0])[0] > 100, 'top view: east is right');
assert.ok(top([0, 1, 0])[1] < 100, 'top view: north is up');
assert.ok(top([0, 0, 3])[2] < top([0, 0, 0])[2], 'top view: higher objects are nearer (drawn later)');
const south = V.makeProjector({ ...base, yaw: 0, pitch: 0 }, 200, 200);
assert.ok(south([0, 0, 1])[1] < 100, 'side view from south: up is up');
assert.ok(south([0, 5, 0])[2] > south([0, 0, 0])[2], 'side view from south: north is farther');
const east = V.makeProjector({ ...base, yaw: Math.PI / 2, pitch: 0 }, 200, 200);
assert.ok(east([1, 0, 0])[2] < east([0, 0, 0])[2], 'view from east: east is nearer');
assert.ok(east([0, 1, 0])[0] > 100, 'view from east: north is to the right');
// shadow of a 2 m pole with sun at 45 deg elevation due south is 2 m north
const s = V.sunVec(45, 180), sh = V.shadowOnPlane([0, 0, 2], s, 0);
assert.ok(Math.abs(sh[1] - 2) < 1e-9 && Math.abs(sh[0]) < 1e-9, 'shadow length = h / tan(elev)');
assert.strictEqual(V.shadowOnPlane([0, 0, 2], V.sunVec(-5, 180), 0), null, 'no shadow at night');
assert.strictEqual(V.hull([[0, 0], [1, 0], [1, 1], [0, 1], [0.5, 0.5]]).length, 4);
const faces = V.buildScene({ roof: [[0, 0], [10, 0], [10, 5], [0, 5]], parapet_height_m: 1, obstacles: [{ polygon: [[1, 1], [2, 1], [2, 2]], height_m: 2 }],
  panels3d: [[[3, 3, 0.8], [4, 3, 0.8], [4, 2, 0.3], [3, 2, 0.3]]] });
assert.strictEqual(faces.filter(f => f.kind === 'wall').length, 4);
assert.strictEqual(faces.filter(f => f.kind === 'obstacle').length, 4);
assert.strictEqual(faces.filter(f => f.kind === 'module').length, 1);
console.log('viewer3d geometry tests passed');
