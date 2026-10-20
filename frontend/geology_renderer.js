import * as THREE from 'three';
import {connectionGeometry} from './geology_geometry.js';

const colors={fracture:0xdb7c34,lithology:0x2095a4};
export class GeologyLayer {
  constructor(owner){this.owner=owner;this.group=new THREE.Group();owner.scene.add(this.group);this.entries=[];}

  update(data){
    this.owner.clearGroup(this.group);this.entries=[];
    const geology=data?.geology;
    if(!geology)return;
    const settings=geology.settings||{};
    for(const connection of geology.connections||[]){
      const strip=connectionGeometry(data,connection);
      if(!strip)continue;
      const group=new THREE.Group();
      const geometry=new THREE.BufferGeometry();
      geometry.setAttribute('position',new THREE.Float32BufferAttribute(strip.positions,3));
      geometry.computeVertexNormals();
      const color=colors[connection.kind]||colors.lithology;
      const mesh=new THREE.Mesh(geometry,new THREE.MeshBasicMaterial({color,side:THREE.DoubleSide,
        transparent:true,opacity:settings.opacity??.35,depthWrite:false}));
      mesh.userData.connectionId=connection.id;
      group.add(mesh);
      const boundary=new THREE.Group();
      for(const path of [strip.left,strip.right]){
        boundary.add(this.owner.line(path,color,.95,strip.closed));
      }
      group.add(boundary);boundary.visible=settings.show_source_lines!==false;
      // Show only a few longitudinal rails so the interpolation remains legible.
      for(const i of [0,Math.floor(strip.left.length/2),strip.left.length-1]){
        group.add(this.owner.line([strip.left[i],strip.right[i]],color,.52));
      }
      this.group.add(group);
      this.entries.push({connection,group,strip});
    }
    this.updateVisibility(data,this.owner.view,this.owner.selectedId);
  }

  updateVisibility(data,view,selectedId){
    const mode=data?.geology?.settings?.mode||'none';
    for(const entry of this.entries){
      const {connection,strip}=entry;
      entry.group.visible=mode!=='none'&&(mode==='both'||mode===connection.kind)&&connection.visible!==false&&
        strip.fromSection.visible&&strip.toSection.visible&&!(view==='front'&&selectedId);
    }
  }

  dispose(){this.owner.clearGroup(this.group);this.group.removeFromParent();this.entries=[];}
}
