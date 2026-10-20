import assert from 'node:assert/strict';
import {imagePointToWorld,resamplePath,alignPaths,connectionGeometry} from '../frontend/geology_geometry.js';
import {Object3D,Vector3} from '../frontend/vendor/three.core.js';
const scene={start_chainage_m:100,height_m:10,sections:[]};
const a={id:'A',chainage_m:110,offset_u_m:2,offset_v_m:1,display_width_m:8,display_height_m:6,rotation_deg:0,flip_horizontal:false,visible:true};
assert.deepEqual(imagePointToWorld(scene,a,[.5,.5]),[10,5,2]);
assert.deepEqual(imagePointToWorld(scene,a,[0,0]),[10,8,-2]);
assert.deepEqual(imagePointToWorld(scene,{...a,flip_horizontal:true},[0,0]),[10,8,6]);
const rotated=imagePointToWorld(scene,{...a,rotation_deg:90},[1,.5]);
assert.ok(Math.abs(rotated[1]-9)<1e-9&&Math.abs(rotated[2]-2)<1e-9);
// Verify the independent formula against the same scene-graph transform used by images.
for(const flip_horizontal of [false,true])for(const rotation_deg of [-135,0,35,90]){
  const section={...a,flip_horizontal,rotation_deg};
  const parent=new Object3D();parent.position.set(10,5,2);parent.rotation.y=-Math.PI/2;
  const image=new Object3D();image.rotation.z=rotation_deg*Math.PI/180;parent.add(image);
  const plane=new Object3D();plane.scale.x=flip_horizontal?-1:1;image.add(plane);
  parent.updateMatrixWorld(true);
  for(const [u,v] of [[0,0],[.5,.5],[.8,.15],[1,1]]){
    const expected=new Vector3((u-.5)*8,(.5-v)*6,0).applyMatrix4(plane.matrixWorld).toArray();
    const actual=imagePointToWorld(scene,section,[u,v]);
    assert.ok(actual.every((value,i)=>Math.abs(value-expected[i])<1e-9));
  }
}
assert.deepEqual(resamplePath([[0,0],[0,0],[1,0]],3),[[0,0],[.5,0],[1,0]]);
assert.deepEqual(resamplePath([[0,0],[0,0]],4),[]);
assert.deepEqual(resamplePath([[0,0],[NaN,1]],4),[]);
const line=[[0,0],[.5,.5],[1,1]];
assert.deepEqual(alignPaths(line,[...line].reverse()),line);
assert.deepEqual(alignPaths(line,line,false,true),[...line].reverse());
const ring=[[0,0],[1,0],[1,1],[0,1]];
assert.deepEqual(alignPaths(ring,[[1,1],[1,0],[0,0],[0,1]],true),ring);
const b={...a,id:'B',chainage_m:120};scene.sections=[a,b];
scene.geology={features:{A:{fractures:[{id:'F1',points:line,closed:false}]},B:{fractures:[{id:'F2',points:[...line].reverse(),closed:false}]}}};
const connection={kind:'fracture',from_section_id:'A',to_section_id:'B',from_object_id:'F1',to_object_id:'F2'};
let geometry=connectionGeometry(scene,connection,3);
assert.equal(geometry.positions.length,2*6*3);
assert.deepEqual(geometry.left[0],[10,8,-2]);
assert.deepEqual(geometry.right[0],[20,8,-2]);
assert.ok(geometry.positions.every(Number.isFinite));
scene.geology.features.A.lithology=[{id:'R1',points:ring,closed:true}];
scene.geology.features.B.lithology=[{id:'R2',points:ring,closed:true}];
geometry=connectionGeometry(scene,{...connection,kind:'lithology',from_object_id:'R1',to_object_id:'R2'},4);
assert.equal(geometry.positions.length,4*6*3);
assert.equal(connectionGeometry(scene,{...connection,to_object_id:'missing'}),null);
scene.sections[1]={...b,chainage_m:110};
assert.equal(connectionGeometry(scene,connection),null);
console.log('Geology geometry passed: normalized transforms, flip/rotation, sampling, alignment and open/closed strips.');
