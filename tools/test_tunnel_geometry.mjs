import assert from "node:assert/strict";
import { sectionPosition, profilePoints, formatChainage, niceStep } from "../frontend/tunnel_geometry.js";

const scene = { start_chainage_m: 4970, end_chainage_m: 5030, width_m: 10, height_m: 8 };
assert.deepEqual(sectionPosition(scene, {chainage_m:4980,offset_u_m:2,offset_v_m:1}), [10,4.2,2]);
assert.deepEqual(sectionPosition(scene, {chainage_m:4970,offset_u_m:0,offset_v_m:0}), [0,3.2,0]);
const profile = profilePoints(10,8);
assert.equal(Math.max(...profile.map(p=>p[1])),8);
assert.equal(Math.min(...profile.map(p=>p[1])),0);
assert.equal(Math.max(...profile.map(p=>p[0])) - Math.min(...profile.map(p=>p[0])),10);
assert.deepEqual(profile[0], profile.at(-1));
assert.equal(formatChainage(4970,'RK'),'RK4+970');
assert.equal(formatChainage(5000,'RK'),'RK5+000');
assert.equal(formatChainage(4999.9999,'RK'),'RK5+000');
assert.equal(formatChainage(15.25),'K0+015.25');
assert.equal(formatChainage(-5),'K−0+005');
assert.equal(niceStep(60),10);
assert.ok(niceStep(.1)>0);
console.log('Tunnel display geometry: position, profile, chainage and tick checks passed.');
