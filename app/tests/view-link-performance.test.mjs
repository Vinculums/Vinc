import test from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import * as THREE from "three";

const source = async entry => {
  const result = await build({ entryPoints: [fileURLToPath(new URL(entry, import.meta.url))],
    bundle: true, platform: "node", format: "esm", write: false });
  return import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString("base64")}`);
};
const { CurvedLinkObject } = await source("../src/surfaces/view-curves3d.ts");
const { RoutedLinkObject } = await source("../src/surfaces/view-routes3d.ts");
const { spatialClearance } = await source("../src/view-layout-obstacles.ts");
const { viewPreferenceWork, canUpdateViewStyle } = await source("../src/view-controls.ts");
const a = { x: 0, y: 0, z: 0 }, b = { x: 10, y: 0, z: 2 };
const curve = new THREE.QuadraticBezierCurve3(new THREE.Vector3(), new THREE.Vector3(5, 4, 2), new THREE.Vector3(10, 0, 2));
const close = (actual, expected, tolerance = 2e-6) => {
  assert.equal(actual.length, expected.length);
  for (let i = 0; i < actual.length; i++) assert(Math.abs(actual[i] - expected[i]) <= tolerance, `vertex ${i}: ${actual[i]} != ${expected[i]}`);
};

test("native curve buffers match the original line and quantized TubeGeometry", () => {
  const object = new CurvedLinkObject();
  object.update(a, b, curve, 0, "#334455");
  const line = object.children.find(c => c.type === "Line");
  close(line.geometry.getAttribute("position").array, new THREE.BufferGeometry().setFromPoints(curve.getPoints(30)).getAttribute("position").array);
  object.update(a, b, curve, .32, "#33445580");
  const tube = object.children.find(c => c.type === "Mesh");
  assert.deepEqual(tube.geometry.parameters.path.toJSON(),curve.toJSON());
  const expected = new THREE.TubeGeometry(curve, 30, .2, 6, false);
  close(tube.geometry.getAttribute("position").array, expected.getAttribute("position").array);
  close(tube.geometry.getAttribute("normal").array, expected.getAttribute("normal").array);
  assert.deepEqual([...tube.geometry.index.array], [...expected.index.array]);
  assert.equal(tube.material.type, "MeshLambertMaterial");
  assert.equal(tube.material.opacity, .85 * 128 / 255);
  assert.equal(tube.material.depthWrite, false);
});

test("native width changes retain geometry and hidden tubes cannot be picked", () => {
  const object = new CurvedLinkObject();object.update(a, b, curve, .32, "#334455");
  const line = object.children.find(c => c.type === "Line"), tube = object.children.find(c => c.type === "Mesh");
  const geometry = tube.geometry, position = geometry.getAttribute("position").array, lineGeometry = line.geometry;
  for (const width of [.48, .72, 0, .32]) {
    object.update(a, b, curve, width, "#445566");
    assert.equal(tube.geometry, geometry);assert.equal(tube.geometry.getAttribute("position").array, position);
    assert.equal(line.geometry, lineGeometry);assert.equal(tube.visible, width > 0);assert.equal(line.visible, width === 0);
    if (width) close(position, new THREE.TubeGeometry(curve, 30, Math.ceil(width * 10) / 20, 6, false).getAttribute("position").array);
  }
  object.update(a, b, curve, 0, "#445566");
  const hits=[];tube.raycast(new THREE.Raycaster(new THREE.Vector3(5, 2, 10), new THREE.Vector3(0,0,-1)),hits);
  assert.equal(hits.length,0);
});

test("native endpoint drags invalidate only the geometry that moved", () => {
  const object = new CurvedLinkObject();object.update(a,b,curve,.32,"#334455");
  const tube = object.children.find(c=>c.type==="Mesh"), previous=tube.geometry;
  const start={x:1,y:2,z:3}, moved=new THREE.QuadraticBezierCurve3(new THREE.Vector3(1,2,3),new THREE.Vector3(5,4,2),new THREE.Vector3(10,0,2));
  object.update(start,b,moved,.32,"#334455");assert.notEqual(tube.geometry,previous);
  close(tube.geometry.getAttribute("position").array,new THREE.TubeGeometry(moved,30,.2,6,false).getAttribute("position").array);
});

test("line/tube crossings release inactive GPU buffers without changing CPU identity", () => {
  const object=new CurvedLinkObject();object.update(a,b,curve,0,"#334455");
  const line=object.children.find(c=>c.type==="Line"),lineGeometry=line.geometry;
  let lineDisposals=0;lineGeometry.addEventListener("dispose",()=>lineDisposals++);
  object.update(a,b,curve,.32,"#334455");assert.equal(lineDisposals,1);
  const tube=object.children.find(c=>c.type==="Mesh"),tubeGeometry=tube.geometry;let tubeDisposals=0;
  tubeGeometry.addEventListener("dispose",()=>tubeDisposals++);
  object.update(a,b,curve,.48,"#334455");assert.equal(tubeDisposals,0);assert.equal(lineDisposals,1);
  object.update(a,b,curve,0,"#334455");assert.equal(tubeDisposals,1);
  object.update(a,b,curve,.32,"#334455");assert.equal(line.geometry,lineGeometry);assert.equal(tube.geometry,tubeGeometry);
  close(tubeGeometry.getAttribute("position").array,new THREE.TubeGeometry(curve,30,.2,6,false).getAttribute("position").array);
});

test("self-loop and large-coordinate tubes preserve native geometry across widths", () => {
  for (const offset of [0, 10000]) {
    const start={x:offset,y:offset,z:offset};
    const loop=new THREE.CubicBezierCurve3(new THREE.Vector3(offset,offset,offset),
      new THREE.Vector3(offset+4,offset+7,offset+2),new THREE.Vector3(offset-4,offset+7,offset+2),new THREE.Vector3(offset,offset,offset));
    const object=new CurvedLinkObject();
    for(const width of [.32,.48,.72]) {
      object.update(start,start,loop,width,"#334455");
      const tube=object.children.find(c=>c.type==="Mesh"),expected=new THREE.TubeGeometry(loop,30,Math.ceil(width*10)/20,6,false);
      close(tube.geometry.getAttribute("position").array,expected.getAttribute("position").array,offset? .001:2e-6);
      close(tube.geometry.getAttribute("normal").array,expected.getAttribute("normal").array);
      assert.deepEqual([...tube.geometry.index.array],[...expected.index.array]);
    }
  }
});

test("routed width changes preserve route, line buffer and cylinder identities", () => {
  const route={engine:"elk",points:[a,{x:10,y:0,z:0},b]}, object=new RoutedLinkObject(route);
  const paint={color:"#334455",width:.32,arrow:true,particles:false,particleColor:"#445566",visible:true,targetRadius:1};
  object.update(a,b,1,paint,0);
  const line=object.children.find(c=>c.type==="Line"), geometry=line.geometry;
  const cylinders=object.children.filter(c=>c.geometry?.type==="CylinderGeometry");assert.equal(cylinders.length,2);
  let gpuReleases=0;
  for(const c of cylinders)c.geometry.addEventListener("dispose",()=>gpuReleases++);
  for(const width of [.48,0,.72]) {
    object.update(a,b,1,{...paint,width},100);assert.equal(line.geometry,geometry);
    assert.deepEqual(object.children.filter(c=>c.geometry?.type==="CylinderGeometry"),cylinders);
    for(const c of cylinders){assert.equal(c.scale.x,width);assert.equal(c.scale.z,width);assert.equal(c.visible,width>0);}
  }
  assert.equal(gpuReleases,2,"positive widths retain buffers and hairline releases each cylinder once");
  assert.deepEqual(route.points,[a,{x:10,y:0,z:0},b]);
});

// The original empty-cell walk is an independent oracle for the new index.
function originalClearance(nodes) {
  const axes=["x","y","z"],cell=v=>Math.round(v/.33),index=new Map(nodes.map(n=>[[n.x,n.y,n.z].map(cell).join(","),n]));
  return (points,source,target)=>{
    for(let i=1;i<points.length;i++){
      const a=points[i-1],b=points[i],along=axes.find(axis=>Math.abs(a[axis]-b[axis])>1e-9);if(!along)continue;
      const [u,v]=axes.filter(axis=>axis!==along);
      for(let t=Math.min(cell(a[along]),cell(b[along]));t<=Math.max(cell(a[along]),cell(b[along]));t++){
        const p={...a,[along]:t*.33},n=index.get(axes.map(axis=>cell(p[axis])).join(","));if(!n||n.id===source||n.id===target)continue;
        if(Math.abs(a[u]-n[u])<.12&&Math.abs(a[v]-n[v])<.12&&Math.max(a[along],b[along])>n[along]-.12&&Math.min(a[along],b[along])<n[along]+.12)return false;
      }
    }return true;
  };
}
test("occupied-axis queries preserve the original rounded cells and strict clearance boundaries",()=>{
  const nodes=Array.from({length:121},(_,i)=>({id:String(i),x:(i%11-5)*.33,y:(Math.floor(i/11)-5)*.33,z:(i%3-1)*.33}));
  const old=originalClearance(nodes),next=spatialClearance(nodes),axes=["x","y","z"];
  for(let i=0;i<3600;i++){
    const a={...nodes[i%121]},b={...a};a[axes[(i+1)%3]] += [0,.119999,.12,.120001,.165,-.165][i%6];
    b[axes[(i+1)%3]]=a[axes[(i+1)%3]];b[axes[i%3]]=(i%41-20)*.33;
    assert.equal(next([a,b],String(i%121),String((i+17)%121)),old([a,b],String(i%121),String((i+17)%121)),`case ${i}`);
  }
});
test("long sparse routes query the occupied range and duplicate centres still fail",()=>{
  const nodes=[{id:"blocked",x:1000000*.33,y:0,z:0}];
  const clear=spatialClearance(nodes);
  assert.equal(clear([a,{x:2000000*.33,y:0,z:0}],"source","target"),false);
  assert.equal(clear([{...a,y:.165},{x:2000000*.33,y:.165,z:0}],"source","target"),true);
  assert.throws(()=>spatialClearance([{id:"a",...a},{id:"b",x:.01,y:0,z:0}]),/centres overlap/);
});
test("link modes are live and pending snapshots still restart",()=>{
  assert.equal(canUpdateViewStyle(["edges"]),false);assert.equal(canUpdateViewStyle(["edges"],true),true);
  const p={view:"3d",layout:"hub"};
  assert.equal(viewPreferenceWork(p,["edges"],{map:true}).mode,"style");
  assert.equal(viewPreferenceWork(p,["edges"],{map:true,current:false}).mode,"remount");
});
