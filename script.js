// frontend/script.js
const video = document.getElementById("video");
const canvas = document.getElementById("canvas");
const captureBtn = document.getElementById("captureBtn");
const uploadInput = document.getElementById("upload");
const detectedEl = document.getElementById("detected");
const rawEl = document.getElementById("raw");
const saveBtn = document.getElementById("save");
const listEl = document.getElementById("list");
const refreshBtn = document.getElementById("refresh");
const exportPdfBtn = document.getElementById("exportPdf");
const compareBtn = document.getElementById("compareBtn");
const compareResult = document.getElementById("compareResult");

const API_BASE = "/api"; // same origin, backend serves frontend

let selectedForCompare = [null, null];
let appliances = [];

// start camera
async function startCamera() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false });
    video.srcObject = stream;
    await video.play();
  } catch (e) {
    console.warn("Camera error", e);
  }
}
startCamera();

// helper to send image blob to /api/ocr
async function sendBlobForOcr(blob) {
  const fd = new FormData();
  fd.append("image", blob, "capture.jpg");
  const res = await fetch(`${API_BASE}/ocr`, { method: "POST", body: fd });
  const json = await res.json();
  return json;
}

captureBtn.onclick = async () => {
  const w = video.videoWidth || 640;
  const h = video.videoHeight || 480;
  canvas.width = w; canvas.height = h;
  const ctx = canvas.getContext("2d");
  ctx.drawImage(video, 0, 0, w, h);
  canvas.toBlob(async (blob) => {
    if (!blob) return;
    const result = await sendBlobForOcr(blob);
    handleOcrResult(result);
  }, "image/jpeg", 0.9);
};

uploadInput.onchange = async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const result = await sendBlobForOcr(file);
  handleOcrResult(result);
};

function handleOcrResult(res) {
  if (!res) return;
  detectedEl.textContent = res.estimated_kwh_per_year ?? "—";
  rawEl.textContent = res.raw_text || JSON.stringify(res);
}

// Save appliance
saveBtn.onclick = async () => {
  const name = document.getElementById("name").value || "Unnamed";
  const price = document.getElementById("price").value || 0;
  const rate = document.getElementById("rate").value || 0;
  const manual = document.getElementById("manualAec").value || "";
  const aec = manual || detectedEl.textContent || "";

  if (!aec) return alert("Please provide AEC (detected or manual)");
  const fd = new FormData();
  fd.append("name", name);
  fd.append("price", price);
  fd.append("energy_rate", rate);
  fd.append("aec", aec);

  const res = await fetch(`${API_BASE}/add_appliance`, { method: "POST", body: fd });
  const json = await res.json();
  alert(json.message || "Saved");
  fetchList();
};

// fetch saved appliances
async function fetchList() {
  const res = await fetch(`${API_BASE}/list_appliances`);
  const data = await res.json();
  appliances = data;
  renderList();
}
refreshBtn.onclick = fetchList;
fetchList();

function renderList() {
  listEl.innerHTML = "";
  appliances.forEach(a => {
    const div = document.createElement("div");
    div.className = "item";
    const left = document.createElement("div");
    left.innerHTML = `<strong>${escapeHtml(a.name)}</strong><div style="font-size:12px;color:#666">${a.energy_kwh} kWh/year • ₹${a.price}</div>`;
    const right = document.createElement("div");
    const btnA = document.createElement("button");
    btnA.textContent = "Slot A";
    btnA.style.marginRight = "6px";
    btnA.onclick = () => toggleSlot(0, a.id, btnA);
    const btnB = document.createElement("button");
    btnB.textContent = "Slot B";
    btnB.onclick = () => toggleSlot(1, a.id, btnB);
    right.appendChild(btnA); right.appendChild(btnB);
    div.appendChild(left); div.appendChild(right);
    listEl.appendChild(div);
  });
}

function toggleSlot(slot, id, btn) {
  if (selectedForCompare[slot] === id) selectedForCompare[slot] = null;
  else selectedForCompare[slot] = id;
  // update UI small
  fetchList();
  highlightSelected();
}

function highlightSelected() {
  // naive highlight: re-render and add style where selected
  const items = Array.from(listEl.children);
  items.forEach((el, idx) => {
    const a = appliances[idx];
    if (!a) return;
    if (selectedForCompare.includes(a.id)) {
      el.style.border = "2px solid #0b74ff"; el.style.background = "#f0f7ff";
    } else { el.style.border = ""; el.style.background = ""; }
  });
}

compareBtn.onclick = async () => {
  if (!selectedForCompare[0] || !selectedForCompare[1]) return alert("Choose two appliances using Slot A and Slot B");
  const res = await fetch(`${API_BASE}/compare`, {
    method: "POST", headers: {"Content-Type":"application/json"},
    body: JSON.stringify({ ids: selectedForCompare })
  });
  const json = await res.json();
  displayCompare(json);
};

function displayCompare(data) {
  compareResult.innerHTML = `<pre>${escapeHtml(JSON.stringify(data, null, 2))}</pre>`;
}

exportPdfBtn.onclick = async () => {
  const res = await fetch(`${API_BASE}/export_pdf`);
  if (!res.ok) return alert("PDF export failed");
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a"); a.href = url; a.download = "WattCompare_Report.pdf"; document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
};

// small helper
function escapeHtml(s){ if(!s) return ""; return s.toString().replace(/[&<>"']/g, c=>({ '&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":\"&#39;\" })[c]); }
