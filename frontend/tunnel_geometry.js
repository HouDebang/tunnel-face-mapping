// Display-only straight alignment. No image measurements are modified here.
export function sectionPosition(scene, section) {
  return [section.chainage_m - scene.start_chainage_m,
    scene.height_m * .4 + section.offset_v_m, section.offset_u_m];
}

export function profilePoints(width, height, segments = 32) {
  const points = [[-width / 2, 0], [width / 2, 0], [width / 2, height * .45]];
  for (let i = 1; i <= segments; i++) {
    const angle = Math.PI * i / segments;
    points.push([Math.cos(angle) * width / 2, height * .45 + Math.sin(angle) * height * .55]);
  }
  points.push([-width / 2, 0]);
  return points;
}

export function formatChainage(value, prefix = "K") {
  const rounded = Math.round(Math.abs(value) * 1000) / 1000;
  const kilometers = Math.floor(rounded / 1000);
  const remainder = (rounded - kilometers * 1000).toFixed(3).replace(/\.?0+$/, "");
  const [integer, decimals] = remainder.split(".");
  return `${prefix}${value < 0 ? "−" : ""}${kilometers}+${integer.padStart(3, "0")}${decimals ? "." + decimals : ""}`;
}

export function niceStep(length) {
  const desired = Math.max(length / 6, .01);
  const power = 10 ** Math.floor(Math.log10(desired));
  return [1, 2, 5, 10].find(value => value * power >= desired) * power;
}
