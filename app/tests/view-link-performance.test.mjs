import test from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const source = async entry => {
  const result = await build({ entryPoints: [fileURLToPath(new URL(entry, import.meta.url))],
    bundle: true, platform: "node", format: "esm", write: false });
  return import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString("base64")}`);
};
const { spatialClearance } = await source("../src/view-layout-obstacles.ts");
const a = { x: 0, y: 0, z: 0 };

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
