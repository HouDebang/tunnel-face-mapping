import {sectionPosition} from './tunnel_geometry.js';

// Geometry below is only a display interpolation between nominated observations.
export function imagePointToWorld(scene, section, [u,v]) {
  let x=(u-.5)*section.display_width_m;
  const y=(.5-v)*section.display_height_m;
  if(section.flip_horizontal)x=-x;
  const angle=section.rotation_deg*Math.PI/180;
  const [cx,cy,cz]=sectionPosition(scene,section);
  return [cx,cy+x*Math.sin(angle)+y*Math.cos(angle),cz+x*Math.cos(angle)-y*Math.sin(angle)];
}

const distance=(a,b)=>Math.hypot(a[0]-b[0],a[1]-b[1]);
export function resamplePath(points, count=40, closed=false) {
  if(!Array.isArray(points)||points.length<2)return [];
  const path=[];
  for(const point of points){
    if(!Array.isArray(point)||point.length!==2||!point.every(Number.isFinite))return [];
    if(!path.length||distance(point,path.at(-1))>1e-10)path.push([...point]);
  }
  if(path.length<2)return [];
  if(closed&&distance(path[0],path.at(-1))>1e-10)path.push([...path[0]]);
  const lengths=[0];
  for(let i=1;i<path.length;i++)lengths.push(lengths.at(-1)+distance(path[i],path[i-1]));
  const total=lengths.at(-1);
  if(total<1e-10)return [];
  const output=[];let index=1;
  for(let i=0;i<count;i++){
    const target=total*i/(closed?count:count-1);
    while(index<path.length-1&&lengths[index]<target)index++;
    const t=(target-lengths[index-1])/(lengths[index]-lengths[index-1]);
    output.push(path[index-1].map((value,j)=>value+(path[index][j]-value)*t));
  }
  return output;
}

export function alignPaths(first, second, closed=false, reverse=false) {
  if(first.length!==second.length||first.length<2)return [];
  let best=[],cost=Infinity;
  // Closed boundaries can start anywhere; compare both winding directions.
  for(const path of [second,[...second].reverse()]){
    for(let shift=0;shift<(closed?path.length:1);shift++){
      const candidate=path.map((_,i)=>path[(i+shift)%path.length]);
      const error=first.reduce((sum,p,i)=>sum+distance(p,candidate[i])**2,0);
      if(error<cost){cost=error;best=candidate;}
    }
  }
  return reverse?[...best].reverse():best;
}

export function connectionGeometry(scene, connection, samples=40) {
  const a=scene.sections.find(s=>s.id===connection.from_section_id);
  const b=scene.sections.find(s=>s.id===connection.to_section_id);
  if(!a||!b||a.chainage_m===b.chainage_m)return null;
  const key=connection.kind==='fracture'?'fractures':'lithology';
  const features=scene.geology?.features||{};
  const first=features[a.id]?.[key]?.find(f=>f.id===connection.from_object_id);
  const second=features[b.id]?.[key]?.find(f=>f.id===connection.to_object_id);
  if(!first||!second)return null;
  const closed=Boolean(first.closed&&second.closed);
  const pa=resamplePath(first.points,samples,closed);
  const pb=resamplePath(second.points,samples,closed);
  const aligned=alignPaths(pa,pb,closed,Boolean(connection.reverse_to));
  if(pa.length<2||!aligned.length)return null;
  const left=pa.map(p=>imagePointToWorld(scene,a,p));
  const right=aligned.map(p=>imagePointToWorld(scene,b,p));
  const positions=[];
  for(let i=0;i<(closed?left.length:left.length-1);i++){
    const next=(i+1)%left.length;
    positions.push(...left[i],...right[i],...right[next],...left[i],...right[next],...left[next]);
  }
  return {positions,left,right,closed,fromSection:a,toSection:b};
}
