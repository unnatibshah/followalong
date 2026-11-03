// Webcam after consent (getUserMedia at 1280×720) and JPEG frame grabs for checks.
// Without a camera, frames are grabbed from the stand-in scene instead.
import { h } from './ui/dom.js';

let stream = null;
let simulate = null; // dev menu: 'NotAllowedError' pretends the browser blocked the camera

export const devSimulateCamera = (name) => { simulate = name; };
export const hasCamera = () => !!stream;

export async function startCamera() {
  if (stream) return stream;
  if (simulate) { const name = simulate; simulate = null; throw new DOMException('Simulated', name); }
  if (!navigator.mediaDevices?.getUserMedia) throw new DOMException('No camera API', 'NotFoundError');
  // facingMode: a phone uses its back camera (pointed at the work); laptops ignore it
  stream = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: { ideal: 'environment' } }, audio: false });
  return stream;
}

export function stopCamera() {
  if (stream) stream.getTracks().forEach((t) => t.stop());
  stream = null;
}

export function cameraView() {
  const v = h('video', { class: 'camera-video', muted: true, autoplay: true, playsInline: true, 'aria-label': 'Your camera' });
  v.srcObject = stream;
  return v;
}

/** A JPEG (longest side 1280, the source's shape) of a <video> or recorded <img>; 1280×720 for a stand-in <svg>. */
export async function grabFrame(source) {
  const canvas = Object.assign(document.createElement('canvas'), { width: 1280, height: 720 });
  const ctx = canvas.getContext('2d');
  if ((source instanceof HTMLVideoElement && source.videoWidth) || (source instanceof HTMLImageElement && source.naturalWidth)) {
    // keep the camera's shape (a phone held upright sends a tall picture): longest side 1280, never stretched
    const w = source.videoWidth || source.naturalWidth, hgt = source.videoHeight || source.naturalHeight;
    const k = 1280 / Math.max(w, hgt);
    canvas.width = Math.round(w * k); canvas.height = Math.round(hgt * k);
    ctx.drawImage(source, 0, 0, canvas.width, canvas.height);
  } else if (source instanceof SVGElement) {
    const clone = source.cloneNode(true);
    clone.querySelectorAll('.scene-hand, .scene-outline, .scene-tag, .scene-pulse').forEach((n) => n.remove());
    clone.setAttribute('width', 1280);
    clone.setAttribute('height', 720);
    const url = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(clone)], { type: 'image/svg+xml' }));
    try {
      const img = new Image();
      img.src = url;
      await img.decode();
      ctx.drawImage(img, 0, 0, 1280, 720);
    } finally { URL.revokeObjectURL(url); }
  }
  return new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.85));
}

/** Light and focus of the camera picture, measured here on a 320-pixel copy (no network): mean brightness (0–255)
 *  and sharpness = the 99th percentile of the step between neighbouring pixels (strong edges survive only when in
 *  focus). Calibrated on 156 real tutorial and YouTube frames: sharp ones are brighter than 54 and sharper than 21
 *  (5th percentiles), so the thresholds flag only a clearly dark or clearly blurred picture; nagging costs more than
 *  a missed mild blur. null before the camera has a picture. */
export const FRAMING = { dark: 45, blurry: 12 };
export function frameQuality(video) {
  if (!video?.videoWidth) return null;
  const w = 320, hgt = Math.max(1, Math.round((320 * video.videoHeight) / video.videoWidth));
  const c = Object.assign(document.createElement('canvas'), { width: w, height: hgt });
  const ctx = c.getContext('2d', { willReadFrequently: true });
  ctx.drawImage(video, 0, 0, w, hgt);
  const d = ctx.getImageData(0, 0, w, hgt).data;
  const luma = new Float32Array(w * hgt);
  let sum = 0;
  for (let i = 0; i < luma.length; i++) { luma[i] = 0.299 * d[i * 4] + 0.587 * d[i * 4 + 1] + 0.114 * d[i * 4 + 2]; sum += luma[i]; }
  const hist = new Uint32Array(256);
  for (let y = 0; y < hgt; y++) for (let x = 1; x < w; x++) hist[Math.min(255, Math.round(Math.abs(luma[y * w + x] - luma[y * w + x - 1])))]++;
  let left = Math.floor(hgt * (w - 1) * 0.01), sharpness = 255;
  while (sharpness > 0 && (left -= hist[sharpness]) > 0) sharpness--;
  const brightness = sum / luma.length;
  return { brightness: Math.round(brightness), sharpness, dark: brightness < FRAMING.dark,
    blurry: brightness >= FRAMING.dark && sharpness < FRAMING.blurry };
}
