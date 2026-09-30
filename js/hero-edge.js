// Draws the ":description" edge from the photo to the intro box on the home page.
// The box is regular HTML, so its size changes with the text and the window;
// this measures where it ended up and points the edge at its right-hand node dot.
(function () {
  var svg = document.querySelector(".kg-wide");
  var box = document.querySelector(".hero .desc-box");
  if (!svg || !box) return;
  var edge = svg.querySelector(".kg-desc path");
  var label = svg.querySelector(".kg-desc text");
  var me = svg.querySelector("clipPath circle");
  if (!edge || !label || !me) return;
  label.textContent = ":description";

  function draw() {
    if (getComputedStyle(svg).display === "none") return;
    var ctm = svg.getScreenCTM();
    if (!ctm) return;
    var inv = ctm.inverse();
    var r = box.getBoundingClientRect();
    // just right of the dot on the box's right edge
    var end = new DOMPoint(r.right + 8, r.top + r.height / 2).matrixTransform(inv);
    var cx = +me.getAttribute("cx"), cy = +me.getAttribute("cy"), R = +me.getAttribute("r") + 11;
    var dx = end.x - cx, dy = end.y - cy, L = Math.hypot(dx, dy);
    var sx = cx + dx / L * R, sy = cy + dy / L * R;
    var ex = end.x, ey = end.y;
    var vx = ex - sx, vy = ey - sy, len = Math.hypot(vx, vy);
    var nx = -vy / len, ny = vx / len, bow = len * 0.18;
    var c1x = sx + vx * 0.33 + nx * bow, c1y = sy + vy * 0.33 + ny * bow;
    var c2x = sx + vx * 0.72 + nx * bow * 0.7, c2y = sy + vy * 0.72 + ny * bow * 0.7;
    edge.setAttribute("d", "M" + sx + "," + sy + " C" + c1x + "," + c1y + " " + c2x + "," + c2y + " " + ex + "," + ey);
    // label above the middle of the curve
    var mx = 0.125 * sx + 0.375 * c1x + 0.375 * c2x + 0.125 * ex;
    var my = 0.125 * sy + 0.375 * c1y + 0.375 * c2y + 0.125 * ey;
    label.setAttribute("x", mx);
    label.setAttribute("y", Math.min(my, sy, ey) - 10);
  }

  draw();
  window.addEventListener("resize", draw);
  window.addEventListener("load", draw);
  if (window.ResizeObserver) new ResizeObserver(draw).observe(box);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(draw);
})();
