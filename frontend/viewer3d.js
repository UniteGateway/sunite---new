/* SUNITE 3-D viewer: dependency-free canvas renderer (orbit, zoom, presets, sun & shadows).
   World frame: x = east, y = north, z = up (metres) - same as the engine. */
(function (root) {
  const V = {
    rotZ(p, a) { const c = Math.cos(a), s = Math.sin(a); return [p[0] * c - p[1] * s, p[0] * s + p[1] * c, p[2]]; },
    rotX(p, a) { const c = Math.cos(a), s = Math.sin(a); return [p[0], p[1] * c - p[2] * s, p[1] * s + p[2] * c]; },
    sub(a, b) { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; },
    cross(a, b) { return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]; },
    dot(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; },
    norm(a) { const l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / l, a[1] / l, a[2] / l]; },
  };
  /* camera: yaw about z, pitch about x; returns [screenX, screenY, depth] */
  function makeProjector(cam, w, h) {
    return p => {
      let q = V.sub(p, cam.target);
      q = V.rotZ(q, -cam.yaw);
      q = V.rotX(q, cam.pitch);   /* pitch = camera elevation: 0 = horizontal view, pi/2 = straight down */
      const s = cam.scale;
      if (cam.perspective) {
        const d = cam.distance, f = d / Math.max(0.1, d - q[1]);
        return [w / 2 + q[0] * s * f, h / 2 - q[2] * s * f, q[1]];
      }
      return [w / 2 + q[0] * s, h / 2 - q[2] * s, q[1]];
    };
  }
  function sunVec(elev, az) { const e = elev * Math.PI / 180, a = az * Math.PI / 180; return [Math.cos(e) * Math.sin(a), Math.cos(e) * Math.cos(a), Math.sin(e)]; }
  function shadowOnPlane(pt, sun, zPlane) { /* project point along -sun onto plane z = zPlane */
    if (sun[2] <= 0.01) return null; const t = (pt[2] - zPlane) / sun[2]; return [pt[0] - sun[0] * t, pt[1] - sun[1] * t, zPlane];
  }
  function hull(pts) {
    const P = pts.map(p => [p[0], p[1]]).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    const cr = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
    const lo = [], up = [];
    for (const p of P) { while (lo.length >= 2 && cr(lo[lo.length - 2], lo[lo.length - 1], p) <= 0) lo.pop(); lo.push(p); }
    for (const p of P.slice().reverse()) { while (up.length >= 2 && cr(up[up.length - 2], up[up.length - 1], p) <= 0) up.pop(); up.push(p); }
    return lo.slice(0, -1).concat(up.slice(0, -1));
  }
  function buildScene(d) {
    const faces = [], roof = d.roof.map(p => [p[0], p[1], 0]);
    faces.push({ pts: roof, fill: '#dfe7f1', stroke: '#2b4c74', kind: 'roof' });
    const ph = d.parapet_height_m || 0;
    if (ph > 0) roof.forEach((a, i) => { const b = roof[(i + 1) % roof.length];
      faces.push({ pts: [a, b, [b[0], b[1], ph], [a[0], a[1], ph]], fill: '#c9d4e2', stroke: '#8798ad', kind: 'wall' }); });
    (d.obstacles || []).forEach(o => {
      const h = o.height_m || 0, base = o.polygon.map(p => [p[0], p[1], 0]), top = o.polygon.map(p => [p[0], p[1], h]);
      base.forEach((a, i) => { const b = base[(i + 1) % base.length];
        faces.push({ pts: [a, b, [b[0], b[1], h], [a[0], a[1], h]], fill: '#b9a28a', stroke: '#6f5a45', kind: 'obstacle' }); });
      faces.push({ pts: top, fill: '#d5bfa6', stroke: '#6f5a45', kind: 'obstacle', label: o.name });
    });
    (d.panels3d || []).forEach((c, i) => faces.push({ pts: c, fill: '#1f4f8f', stroke: '#9cc3f5', kind: 'module',
      shade: (d.panel_shading_pct || [])[i] || 0 }));
    return faces;
  }
  function render(ctx, w, h, faces, cam, opts) {
    const proj = makeProjector(cam, w, h);
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = opts.dark ? '#0f1b2d' : '#f4f8fc'; ctx.fillRect(0, 0, w, h);
    const sun = opts.sun ? sunVec(opts.sun.elevation_deg, opts.sun.azimuth_deg) : null;
    const items = faces.map(f => ({ f, P: f.pts.map(proj) }));
    items.forEach(it => { it.depth = it.P.reduce((s, p) => s + p[2], 0) / it.P.length; });
    /* roof drawn first, then shadows on roof, then everything else back-to-front */
    const roofItems = items.filter(i => i.f.kind === 'roof'), rest = items.filter(i => i.f.kind !== 'roof').sort((a, b) => b.depth - a.depth);
    const poly = (P, fill, stroke, lw) => { ctx.beginPath(); P.forEach((p, k) => k ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])); ctx.closePath();
      if (fill) { ctx.fillStyle = fill; ctx.fill(); } if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = lw || 1; ctx.stroke(); } };
    roofItems.forEach(it => poly(it.P, it.f.fill, it.f.stroke, 2));
    if (sun && sun[2] > 0.02) {
      ctx.save(); ctx.globalAlpha = 0.5;
      faces.filter(f => f.kind === 'obstacle' || f.kind === 'module' || f.kind === 'wall').forEach(f => {
        const sh = f.pts.map(p => shadowOnPlane(p, sun, 0)).filter(Boolean);
        if (sh.length >= 3) poly(hull(sh).map(p => proj([p[0], p[1], 0.001])), '#1b2430', null);
      }); ctx.restore();
    }
    rest.forEach(it => {
      let fill = it.f.fill;
      if (it.f.kind === 'module') {
        const n = V.norm(V.cross(V.sub(it.f.pts[1], it.f.pts[0]), V.sub(it.f.pts[3], it.f.pts[0])));
        const lit = sun ? Math.max(0.25, Math.abs(V.dot(n, sun))) : 0.8;
        const s = Math.min(1, (it.f.shade || 0) / 15);
        fill = `rgb(${Math.round((31 + s * 190) * lit + 20)},${Math.round((79 - s * 40) * lit + 20)},${Math.round((143 - s * 110) * lit + 30)})`;
      }
      poly(it.P, fill, it.f.stroke, it.f.kind === 'module' ? 0.6 : 1);
    });
    if (sun && sun[2] > 0) { /* sun direction marker */
      const c = cam.target, far = [c[0] + sun[0] * 30, c[1] + sun[1] * 30, c[2] + sun[2] * 30], a = proj(c), b = proj(far);
      ctx.strokeStyle = '#f28c0f'; ctx.setLineDash([6, 4]); ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle = '#f7a400'; ctx.beginPath(); ctx.arc(b[0], b[1], 9, 0, 7); ctx.fill();
    }
    /* north arrow */
    const n0 = proj([cam.target[0], cam.target[1], 0]), n1 = proj([cam.target[0], cam.target[1] + 5, 0]);
    const dx = n1[0] - n0[0], dy = n1[1] - n0[1], L = Math.hypot(dx, dy) || 1;
    ctx.save(); ctx.translate(w - 40, 40); ctx.strokeStyle = ctx.fillStyle = '#f28c0f'; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(-dx / L * 14, -dy / L * 14); ctx.lineTo(dx / L * 14, dy / L * 14); ctx.stroke();
    ctx.font = 'bold 12px system-ui'; ctx.fillText('N', dx / L * 22 - 4, dy / L * 22 + 4); ctx.restore();
  }
  function fitCamera(d) {
    const xs = d.roof.map(p => p[0]), ys = d.roof.map(p => p[1]);
    const cx = (Math.min(...xs) + Math.max(...xs)) / 2, cy = (Math.min(...ys) + Math.max(...ys)) / 2;
    const span = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys), 5);
    return { target: [cx, cy, 0], yaw: -0.6, pitch: 0.9, scale: 1, span, distance: span * 2.2, perspective: true };
  }
  const PRESETS = { top: { yaw: 0, pitch: Math.PI / 2, perspective: false }, south: { yaw: 0, pitch: 0.12, perspective: false },
                    east: { yaw: Math.PI / 2, pitch: 0.12, perspective: false }, perspective: { yaw: -0.6, pitch: 0.9, perspective: true } };
  root.SuniteViewer = { makeProjector, sunVec, shadowOnPlane, hull, buildScene, render, fitCamera, PRESETS };
  if (typeof module !== 'undefined') module.exports = root.SuniteViewer;
})(typeof window !== 'undefined' ? window : globalThis);
